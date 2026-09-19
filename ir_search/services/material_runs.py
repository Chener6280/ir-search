"""Explicit private operational records and safe continuation for calling skills."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from ir_search.context import RequestContext, RequestStopped
from ir_search.contracts.materials import MaterialSearchResult, TextScope
from ir_search.infrastructure.material_cursor import _decode, _states
from ir_search.infrastructure.private_files import _private_read, _private_write, _directory_lock
from ir_search.infrastructure.recovery import _recovery, _RULES
from ir_search.registry import DataAdapterError
from .source_diagnostics import _SOURCES


_CODES = frozenset(DataAdapterError.KINDS) | frozenset(_RULES) | {
    'bounded_search_not_exhaustive', 'xhs_search_bounded_not_exhaustive', 'material_has_caveats',
    'research_scope_required', 'material_source_selection_required', 'dry_run_no_source_calls', 'candidate_budget_exhausted',
    'result_limit_reached', 'source_queries_failed', 'source_scan_incomplete',
    'publication_filter_applied_locally', 'publication_date_unknown', 'business_period_not_verified',
    'ima_page_budget_exhausted', 'ima_pagination_unknown', 'wechat_page_budget_exhausted',
    'original_text_fetch_failed', 'not_queried_request_stopped', 'no_match_in_scanned_records',
    'material_source_not_registered', 'unknown_diagnostic',
}


def _code(value):
    return value if isinstance(value, str) and value in _CODES else 'unknown_diagnostic'


def _count(value):
    return value if type(value) is int and 0 <= value <= 1000000 else 0


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _summary(result):
    versions = [v for g in result.items for v in g.get('versions', [])]
    codes = sorted({_code(d.code) for d in result.diagnostics})
    # Positive field selection: never serialize source text, queries, URLs, raw
    # messages, account names, cursors or arbitrary adapter metadata to this log.
    original = result.to_dict()
    original.pop('audit', None)
    return {'schema_version': '1.0', 'operation': 'search_materials',
        'result_fingerprint': hashlib.sha256(_json(original)).hexdigest(),
        'request_fingerprint': hashlib.sha256(_json(result.request.to_dict())).hexdigest(),
        'run_fingerprint': hashlib.sha256(str(result.request_id).encode()).hexdigest(),
        'status': result.status.value, 'complete': bool(result.complete),
        'document_groups': len(result.items), 'source_records': len(versions),
        'text_scope_counts': {scope.value: sum(v.get('text_scope') == scope.value for v in versions) for scope in TextScope},
        'coverage': [{'provider': c.get('provider') if c.get('provider') in _SOURCES else 'custom_provider',
            'state': c.get('state') if c.get('state') in {'queried', 'selected', 'not_registered', 'planned_not_queried', 'excluded_by_request'} else _code(c.get('state')),
            'scanned_count': _count(c.get('scanned_count')), 'matched_count': _count(c.get('matched_count')),
            'continuation_count': len(c.get('continuation_cursors', []))} for c in result.coverage],
        'diagnostics': [{'code': code, **_recovery(code)} for code in codes],
        'unknown_diagnostic_count': sum(_code(d.code) == 'unknown_diagnostic' for d in result.diagnostics),
        'evidence_status': 'operational_summary_not_research_evidence',
        'source_text_included': False, 'automatic_rule_changes': False}


def record_material_run(result, output_dir, *, context=None):
    """Save a bounded, immutable operational summary, without queries or source text.

    Opt-in only. Identical results reuse a verified record. Storage failures are
    reported separately and never turn a successful source read into a failure.
    This records observations, not model reasoning, and does not update skills.
    """
    if not isinstance(result, MaterialSearchResult): raise ValueError('MaterialSearchResult required')
    if (not isinstance(output_dir, (str, Path)) or not str(output_dir).strip()
            or any(ord(c) < 32 for c in str(output_dir))): raise ValueError('Expected private run directory')
    context = context or RequestContext(timeout_seconds=5)
    root = Path(output_dir).expanduser().absolute()
    try:
        context.check_active()
        if any(p.is_symlink() for p in (root, *root.parents)): raise OSError()
        summary = _summary(result)
        raw = _json(summary)
        if len(raw) > 256*1024: raise ValueError()
        run_id = hashlib.sha256(raw).hexdigest()
        with _directory_lock(root, context):
            path = root/(run_id+'.json')
            try:
                existing = json.loads(_private_read(path, 256*1024))
                if existing.get('run_id') != run_id or _json(existing.get('summary')) != raw: raise ValueError()
                state = 'reused'
            except FileNotFoundError:
                files = list(root.glob('*.json'))
                if len(files) >= 1000 or sum(p.lstat().st_size for p in files) > 64*1024*1024:
                    return {'status': 'error', 'code': 'run_store_full'}
                envelope = {'run_id': run_id, 'recorded_at': datetime.now(timezone.utc).isoformat(), 'summary': summary}
                _private_write(path, _json(envelope)); state = 'recorded'
        return {'status': state, 'run_id': run_id, 'path': str(path), 'source_text_included': False}
    except RequestStopped as exc: return {'status': 'error', 'code': exc.code}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return {'status': 'error', 'code': 'run_record_unavailable'}


def next_material_request(result, *, candidates_per_source=None, text_reads_per_source=None, context=None):
    """Build an explicit request for only sources with actual returned cursors.

    None means no continuation was offered, not exhaustive coverage. Does not
    perform I/O or renew expired provider snapshots. Budgets may change; source,
    query, publication/period windows and account binding remain enforced.
    """
    if not isinstance(result, MaterialSearchResult): raise ValueError('MaterialSearchResult required')
    if result.request.dry_run: raise ValueError('A preview cannot be resumed')
    if any(g.get('code') == 'result_limit_reached' for g in result.gaps):
        raise ValueError('Rerun this page with a higher result limit or smaller candidate budget before resuming')
    providers, cursors = [], []
    for coverage in result.coverage:
        for token in coverage.get('continuation_cursors', []):
            state = _decode(token)
            if state['provider'] != coverage.get('provider'): raise DataAdapterError('invalid_cursor')
            if token not in cursors: cursors.append(token)
            if state['provider'] not in providers: providers.append(state['provider'])
    if not cursors: return None
    changes = {'providers': tuple(providers), 'source_cursors': tuple(cursors),
        'symbols': tuple(result.plan.get('symbols', result.request.symbols))}
    if candidates_per_source is not None: changes['candidates_per_source'] = candidates_per_source
    if text_reads_per_source is not None: changes['text_reads_per_source'] = text_reads_per_source
    request = replace(result.request, **changes)
    context = context or RequestContext()
    for provider in providers: _states(request, provider, context)
    return request
