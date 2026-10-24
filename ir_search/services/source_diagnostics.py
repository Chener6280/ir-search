"""Configuration, dependency and explicit live checks without legacy imports."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.util import find_spec
from importlib.metadata import PackageNotFoundError, version
import argparse
import hashlib
import json
import os
from pathlib import Path

from ir_search.context import RequestContext, RequestStopped
from ir_search.infrastructure.credentials import source_configuration_status, SourceConfigError
from ir_search.infrastructure.recovery import _recovery
from ir_search.registry import DataAdapterError


@dataclass(frozen=True)
class _Source:
    operations: tuple[str, ...]
    backends: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    optional_dependencies: tuple[str, ...] = ()
    scope: str = 'bounded_provider_results'


_M = ('search_materials', 'retrieve')
_D = ('get_data',)
_SOURCES = {
    'wind_mysql': _Source(_D, ('mysql',), ('pymysql',)),
    'jydb': _Source(_D+_M, ('mysql',), ('pymysql',)),
    'akshare': _Source(_D, ('configured_akshare_backend',), ('akshare',)),
    'fmp': _Source(_D, ('fmp_stable_api',)),
    'tushare_corpus': _Source(_M, ('official_mcp',)),
    'web': _Source(_M, ('regional_search', 'public_http', 'optional_crawl4ai', 'explicit_scrapling_parser', 'explicit_firecrawl'), optional_dependencies=('crawl4ai','scrapling')),
    'rss': _Source(_M, ('public_rss_atom', 'explicit_original_page_read'), scope='configured_feed_snapshots_not_full_history'),
    'zsxq': _Source(_M, ('official_mcp',), scope='authorized_group_timelines_and_topics'),
    'wechat': _Source(_M, ('dajiala_discovery', 'body_cache', 'public_http', 'optional_crawl4ai', 'dajiala_body'),
                      optional_dependencies=('crawl4ai',), scope='configured_account_timelines'),
    'ima': _Source(_M, ('official_openapi',), scope='authorized_knowledge_bases_and_notes'),
    'wisburg': _Source(_M, ('official_mcp',), scope='stored_summaries_articles_commentary'),
    'alphapai': _Source(_M, ('account_browser',), ('playwright',), scope='shared_meetings'),
    'gangtise': _Source(_M, ('account_http_session',), optional_dependencies=('playwright',)),
    'xhs': _Source(_M, ('local_xiaohongshu_mcp_http',), scope='one_search_snapshot_and_discovered_notes'),
    'xueqiu': _Source(_M, ('bocha_discovery', 'selected_http_or_browser'), optional_dependencies=('playwright',), scope='bounded_public_posts'),
    'eastmoney': _Source(_M, ('bocha_discovery', 'platform_http'), scope='guba_posts_not_market_data'),
    'video': _Source(_M, ('regional_search', 'platform_metadata', 'available_captions'),
                     optional_dependencies=('youtube_transcript_api',), scope='metadata_and_existing_captions_not_transcription'),
    'xiaoyuzhou': _Source(_M, ('bocha_discovery', 'public_metadata', 'explicit_volcengine_asr'),
                          optional_dependencies=('websockets',), scope='metadata_or_explicit_bounded_audio_windows'),
    'sec': _Source(_M, ('sec_edgar',), scope='company_scoped_original_filings'),
    'fiona': _Source(_D, ('official_mcp',), scope='bounded_derivatives_quotes_bars_and_native_risk'),
    'global_macro': _Source(_D, ('official_public_series',), scope='native_frequency_latest_revised_snapshot'),
    'hkex': _Source(_M, ('official_title_search', 'public_http'), scope='active_hk_securities_original_disclosures'),
    'company_ir': _Source(_M, ('reviewed_issuer_directories', 'public_http', 'optional_crawl4ai'), optional_dependencies=('crawl4ai',), scope='curated_issuer_directory_snapshots'),
}


def _private_file_protection(platform=None):
    """Say plainly what this platform verifies; a silent pass would read as 'checked'."""
    platform = platform or os.name
    if platform == 'posix':
        return {'platform': 'posix', 'owner_and_mode_bits_verified': True}
    return {'platform': platform, 'owner_and_mode_bits_verified': False,
            'code': 'permissions_not_verified_on_this_platform',
            'next_action': '凭证、会话和缓存目录的属主与权限位检查只在 POSIX 上执行。请把它们放在本人用户目录下'
                           '（例如 %LOCALAPPDATA%\\ir-search），不要放在盘符根目录或共享目录；'
                           '需要时用 icacls <路径> /inheritance:r /grant:r "%USERNAME%:(OI)(CI)F" 收紧访问。'}


def _credentials_resolution():
    """Read-only: which credentials file this process reads, and what a browser worker reads.

    Paths and one boolean only, never file contents; nothing is created, changed or started.
    A worker never gets the real file: its parent hands it a one-line snapshot of the fake-ip
    opt-in through IR_SEARCH_CREDENTIALS_FILE, which is the first rule of the same lookup.
    """
    from pathlib import Path
    from ir_search.infrastructure import credentials
    from ir_search.infrastructure.public_web import _fake_ip_https_enabled
    from ir_search.infrastructure.web_browser import _SWITCH_SNAPSHOT
    try:
        path = credentials.credentials_path()
        checkout = Path(credentials.__file__).resolve().parents[2]
        rule = ('IR_SEARCH_CREDENTIALS_FILE' if os.environ.get('IR_SEARCH_CREDENTIALS_FILE') else
                'source_checkout' if (checkout / 'pyproject.toml').is_file() else 'working_directory')
        this_process = {'path': str(path), 'selected_by': rule, 'exists': path.is_file()}
    except (ValueError, OSError, RuntimeError):
        this_process = {'code': 'invalid_credentials_path', 'key': 'IR_SEARCH_CREDENTIALS_FILE'}
    worker = {'path': os.path.join('<worker temporary directory>', _SWITCH_SNAPSHOT),
              'selected_by': 'IR_SEARCH_CREDENTIALS_FILE', 'set_by': 'parent_process_at_worker_start',
              'reads_real_credentials_file': False, 'snapshot_keys': ['PUBLIC_WEB_FAKE_IP_HTTPS'],
              'frozen_at': 'worker_start'}
    try:
        worker['snapshot_value_if_started_now'] = 'true' if _fake_ip_https_enabled() else 'false'
    except DataAdapterError as exc:
        # Same rule as the snapshot itself: configuration that cannot be read never waives anything.
        worker.update(snapshot_value_if_started_now='false', code=exc.code)
        try:
            credentials.read_credentials()
            worker['key'] = 'PUBLIC_WEB_FAKE_IP_HTTPS'  # the file reads fine, so it is this value
        except SourceConfigError as unreadable:
            worker['detail_code'] = unreadable.code  # the file itself, not this key
    return {'this_process': this_process, 'browser_worker': worker}


def _dependency(name):
    try: installed = find_spec(name) is not None
    except (ImportError, ValueError, AttributeError): installed = False
    extras = {'pymysql':'mysql', 'akshare':'akshare', 'playwright':'browser', 'crawl4ai':'crawl',
              'youtube_transcript_api':'video', 'websockets':'audio', 'scrapling':'scrapling', 'patchright':'patchright'}
    try: installed_version = version({'pymysql':'PyMySQL', 'youtube_transcript_api':'youtube-transcript-api'}.get(name, name))
    except (PackageNotFoundError, ValueError): installed_version = None
    return {'name': name, 'installed': installed, 'runtime_verified': False,
            'installed_version': installed_version, 'installation_extra': extras.get(name),
            'installation_command': 'python -m pip install "ir-search[' + extras[name] + ']"' if name in extras else None}


def _samples(providers, live, search_samples, read_samples, web_read_mode):
    """Validate every sample before any selected source can be called."""
    from ir_search.contracts.materials import MaterialSearchRequest
    from ir_search.infrastructure.public_web import _url
    from ir_search.infrastructure.platform_documents import _platform_host
    search_samples = {} if search_samples is None else search_samples
    read_samples = {} if read_samples is None else read_samples
    if (not isinstance(search_samples, dict) or not isinstance(read_samples, dict)
            or len(search_samples) > 5 or len(read_samples) > 1
            or not set(search_samples) <= set(providers) or not set(read_samples) <= set(providers)
            or (search_samples or read_samples) and not live
            or web_read_mode not in {'http', 'scrapling', 'browser'}):
        raise ValueError('Samples require explicit live providers and bounded read modes')
    allowed = {'question', 'keywords', 'symbols', 'published_start', 'published_end', 'web_region',
               'wechat_accounts', 'ima_knowledge_base_ids', 'zsxq_group_ids', 'sec_ciks', 'sec_forms', 'video_platforms'}
    requests = {}
    for provider, values in search_samples.items():
        if ('search_materials' not in _SOURCES[provider].operations or not isinstance(values, dict)
                or not set(values) <= allowed or not values.get('published_start') or not values.get('published_end')):
            raise ValueError('Search samples require an explicit query and publication window')
        try:
            requests[provider] = MaterialSearchRequest(**values, providers=(provider,), limit=1, max_sources=1,
                candidates_per_source=1, text_reads_per_source=0, max_chars=1000)
        except (TypeError, ValueError):
            raise ValueError('Invalid bounded search sample') from None
    for provider, url in read_samples.items():
        if provider != 'web' or not isinstance(url, str):
            raise ValueError('Read samples currently support generic public web URLs only')
        try: parsed, host, _ = _url(url)
        except DataAdapterError: raise ValueError('Invalid public sample URL') from None
        if (parsed.scheme != 'https' or parsed.query or parsed.fragment or _platform_host(url)
                or host in {'mp.weixin.qq.com', 'www.xiaohongshu.com', 'www.sec.gov', 'sec.gov'}):
            raise ValueError('Use a credential-free generic HTTPS sample URL')
    return requests, read_samples


def _probe(item, operation, scope, context, report, action):
    observation = item['operation_status'].setdefault(operation, {'state': 'not_probed'})
    observation.update(scope=scope, verification_basis='explicit_bounded_sample')
    try:
        context.check_active()
        report['source_calls_started'] += 1
        details = action()
        context.check_active()
        observation.update(state='passed', **details)
        item['live_probe'] = {'state': 'passed', 'scope': 'see_operation_status'}
        if operation in {'search_materials', 'retrieve', 'get_data'}:
            item[{'search_materials':'search', 'retrieve':'retrieve', 'get_data':'get_data'}[operation] + '_live_verified'] = True
        item['state'] = 'sample_verified'
    except (DataAdapterError, RequestStopped, SourceConfigError) as exc:
        observation.update(state='failed', code=exc.code)
        item['diagnostics'].append({'code': exc.code, 'operation': operation, **_recovery(exc.code)})
        item['state'] = 'probe_failed'
    except Exception:
        observation.update(state='failed', code='probe_failed')
        item['diagnostics'].append({'code': 'probe_failed', 'operation': operation, **_recovery('upstream_schema')})
        item['state'] = 'probe_failed'


def _read_sample(url, mode, context):
    from ir_search.infrastructure.web_documents import read_web_document
    document = read_web_document(url, context=context, mode=mode, max_chars=2000)
    details = document.extra.get('web_read', {})
    state = details.get('content_state')
    if state not in {'article_text', 'document_text'} or not document.text.strip() or document.errors:
        code = 'web_content_' + str(state)
        raise DataAdapterError(code if code in DataAdapterError.KINDS else 'no_extracted_text')
    attempts = details.get('attempts', [])
    failures = [a['state'] for a in attempts if a.get('state') in DataAdapterError.KINDS]
    if failures: raise DataAdapterError(failures[0])
    if mode == 'scrapling' and details.get('html_extraction', {}).get('selected') != 'scrapling':
        raise DataAdapterError('no_extracted_text')
    return {'backend': details['backend'], 'content_state': state, 'text_chars': len(document.text),
            'text_sha256': hashlib.sha256(document.text.encode()).hexdigest(),
            'requested_mode': mode, 'completeness_verified': False}


def _search_sample(request, env_file, context):
    from ir_search.material_registry import build_material_registry
    from ir_search.services.material_search import search_materials
    from ir_search.models import FailureKind
    result = search_materials(request, registry=build_material_registry(env_file=env_file), context=context)
    provider = request.providers[0]
    failures = [d for d in result.diagnostics if d.failure_kind != FailureKind.NONE and d.provider in {None, provider}]
    if failures:
        code = failures[0].code
        raise DataAdapterError(code if code in DataAdapterError.KINDS else 'upstream_schema')
    coverage = next((c for c in result.coverage if c['provider'] == provider), {})
    if coverage.get('state') != 'queried' or result.required_inputs or result.fallback_requests:
        raise DataAdapterError('upstream_schema')
    return {'backend': provider, 'scanned_count': coverage['scanned_count'],
            'matched_count': coverage['matched_count'], 'returned_groups': len(result.items),
            'text_reads_requested': 0, 'complete': result.complete,
            'query_sha256': hashlib.sha256(request.question.encode()).hexdigest(),
            'scope_limit': 'one_candidate_not_full_source_coverage'}


def _feed_sample(env_file, context):
    from ir_search.infrastructure.rss import rss_profile, fetch_feed
    profile = rss_profile(env_file=env_file)
    if profile is None: raise DataAdapterError('source_disabled')
    # Availability of feed XML is separate from executing an investment query.
    pages = [fetch_feed(url, context=context) for url in profile.feed_urls]
    return {'backend': 'public_rss_atom', 'feed_count': len(pages),
            'entry_count': sum(len(page.items) for page in pages), 'complete': False,
            'scope_limit': 'configured_snapshots_not_search_or_history',
            'warning_count': sum(len(page.warnings) for page in pages)}


def diagnose_sources(providers=(), *, env_file=None, live=False, context=None,
                     search_samples=None, read_samples=None, web_read_mode='http'):
    """Report current local readiness; live=True requires explicitly named sources.

    Live checks cover XHS login and configured RSS snapshots. Explicit search
    samples dispatch one candidate with zero body reads (provider charges may
    apply); read_samples accepts one generic public web URL. A passed sample
    verifies only that operation and scope, never historical completeness.
    """
    if (type(live) is not bool or not isinstance(providers, (list, tuple))
            or any(not isinstance(p, str) or p not in _SOURCES for p in providers)
            or len(set(providers)) != len(providers) or live and not providers):
        raise ValueError('Use known, explicit providers for live checks')
    searches, reads = _samples(providers, live, search_samples, read_samples, web_read_mode)
    context = context or RequestContext(timeout_seconds=30, max_operations=10)
    configuration = source_configuration_status(env_file=env_file)
    selected = tuple(providers) or tuple(_SOURCES)
    rows = {s['provider']: s for s in configuration['sources']}
    report = {'schema_version': '1.0', 'checked_at': datetime.now(timezone.utc).isoformat(),
        'verification_basis': 'explicit_live_probe_and_local_metadata' if live else 'local_metadata_only',
        'source_calls_started': 0, 'sources': [], 'diagnostics': configuration['diagnostics'],
        'automatic_configuration_changes': False, 'private_file_protection': _private_file_protection(),
        'credentials_resolution': _credentials_resolution(),
        'source_calls_basis': 'bounded_probe_dispatches_not_http_request_count'}
    for provider in selected:
        spec = _SOURCES[provider]; configured = rows.get(provider, {})
        required = [_dependency(name) for name in spec.dependencies]
        optional = [_dependency(name) for name in spec.optional_dependencies]
        if provider == 'xueqiu' and configured.get('read_mode') == 'browser':
            required, optional = [_dependency(configured.get('browser_driver', 'playwright'))], []
        code = configured.get('code')
        state = ('configuration_error' if code or not configured else
                 'disabled' if not configured.get('enabled') else
                 'dependency_missing' if any(not d['installed'] for d in required) else
                 'configured_unverified')
        # Keep the precise reason and the env key to fix; the generic code stays for older callers.
        detail_code, detail_key = (code, configured.get('key')) if state == 'configuration_error' else (None, None)
        if state == 'configuration_error': code = 'source_config_error'
        elif state == 'disabled': code = 'source_disabled'
        elif state == 'dependency_missing': code = 'dependency_missing'
        item = {'provider': provider, 'state': state, 'configured': bool(configured.get('configured')),
            'operations': list(spec.operations), 'backend_chain': list(spec.backends),
            'backend_chain_basis': 'declared_routes_not_live_selection_or_automatic_failover',
            'active_backend': None, 'required_dependencies': required, 'optional_dependencies': optional,
            'scope': spec.scope, 'live_probe': {'state': 'not_requested', 'scope': None},
            'search_live_verified': False, 'retrieve_live_verified': False, 'get_data_live_verified': False,
            'operation_status': {op: {'state': 'not_probed', 'scope': None,
                'verification_basis': 'not_executed'} for op in spec.operations},
            'diagnostics': []}
        if code:
            diagnostic = {'code': code, **_recovery(code)}
            if detail_code and detail_code != code: diagnostic['detail_code'] = detail_code
            if detail_key: diagnostic['key'] = detail_key
            item['diagnostics'].append(diagnostic)
        if provider == 'xueqiu':
            item['reader_configuration'] = {key: configured[key] for key in (
                'read_mode', 'browser_cookie_mode', 'browser_channel', 'browser_driver', 'browser_headless',
                'browser_experimental', 'browser_runtime_verified') if key in configured}
        if provider in {'web', 'rss'}:
            from ir_search.infrastructure.web_toolkit import web_toolkit_status
            item['reader_configuration'] = web_toolkit_status(env_file=env_file)
        if live:
            item['live_probe']['state'] = 'not_probed'
            if state == 'configured_unverified' and provider == 'xhs':
                try:
                    context.check_active()
                    from ir_search.infrastructure.xhs import XhsClient, xhs_profile
                    profile = xhs_profile(env_file=env_file)
                    if profile is None: raise DataAdapterError('source_disabled')
                    report['source_calls_started'] += 1
                    XhsClient(profile).check_login(context=context)
                    item.update(state='login_verified', active_backend=spec.backends[0])
                    item['live_probe'] = {'state': 'passed', 'scope': 'login_status_only_not_search_or_body'}
                    item['operation_status']['login'] = {'state': 'passed', 'scope': 'login_status_only_not_search_or_body',
                        'verification_basis': 'explicit_live_probe', 'backend': spec.backends[0]}
                except (DataAdapterError, RequestStopped, SourceConfigError) as exc:
                    item['state'] = 'probe_failed'
                    item['live_probe'] = {'state': 'failed', 'scope': 'login_status_only_not_search_or_body'}
                    item['diagnostics'].append({'code': exc.code, **_recovery(exc.code)})
                    item['operation_status']['login'] = {'state': 'failed', 'code': exc.code,
                        'scope': 'login_status_only_not_search_or_body', 'verification_basis': 'explicit_live_probe'}
                except Exception:
                    item['state'] = 'probe_failed'
                    item['live_probe']['state'] = 'failed'
                    item['diagnostics'].append({'code': 'probe_failed', **_recovery('upstream_schema')})
                    item['operation_status']['login'] = {'state': 'failed', 'code': 'probe_failed',
                        'scope': 'login_status_only_not_search_or_body', 'verification_basis': 'explicit_live_probe'}
            if state == 'configured_unverified' and provider == 'rss' and provider not in searches:
                _probe(item, 'feed_snapshot', 'configured_rss_feeds', context, report,
                       lambda: _feed_sample(env_file, context))
            if state == 'configured_unverified' and item['state'] != 'probe_failed' and provider in searches:
                _probe(item, 'search_materials', 'explicit_single_candidate_query', context, report,
                       lambda: _search_sample(searches[provider], env_file, context))
            if state == 'configured_unverified' and item['state'] != 'probe_failed' and provider in reads:
                _probe(item, 'retrieve', 'explicit_generic_public_url', context, report,
                       lambda: _read_sample(reads[provider], web_read_mode, context))
            if item['live_probe']['state'] == 'not_probed' and state == 'configured_unverified' and item['state'] != 'probe_failed':
                item['diagnostics'].append({'code': 'live_probe_not_implemented',
                    'next_action': '提供显式 search_samples/read_samples，或用 get_data/retrieve 样本另行验收。', 'automatic_retry': False})
            if any(v['state'] == 'failed' for v in item['operation_status'].values()):
                item['state'] = 'probe_failed'
                item['live_probe']['state'] = 'failed'
            if item['retrieve_live_verified']:
                item['active_backend'] = item['operation_status']['retrieve']['backend']
        report['sources'].append(item)
    report['operations_used'] = context.operations
    report['status'] = 'partial' if report['diagnostics'] or any(
        s['state'] in {'configuration_error', 'dependency_missing', 'probe_failed'} for s in report['sources']) else 'ok'
    report['status_basis'] = 'diagnostic_report_completed_not_all_sources_live_verified'
    return report


def main(argv=None):
    """CLI equivalent of diagnose_sources; defaults to zero network calls."""
    parser = argparse.ArgumentParser(description='Inspect ir-search sources without exposing credentials')
    parser.add_argument('--provider', action='append', default=[], choices=sorted(_SOURCES))
    parser.add_argument('--live', action='store_true', help='Explicit read-only probe; requires --provider')
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('--read-url', help='One generic public HTTPS sample; requires --live --provider web')
    parser.add_argument('--read-mode', choices=('http', 'scrapling', 'browser'), default='http')
    parser.add_argument('--search-samples', help='JSON file mapping selected providers to bounded search requests')
    args = parser.parse_args(argv)
    if args.live and not args.provider: parser.error('--live requires --provider')
    try:
        samples = None
        if args.search_samples:
            path = Path(args.search_samples)
            if path.stat().st_size > 16384: raise ValueError()
            samples = json.loads(path.read_text(encoding='utf-8'))
        result = diagnose_sources(args.provider, live=args.live, context=RequestContext(timeout_seconds=args.timeout),
            read_samples={'web':args.read_url} if args.read_url else None, search_samples=samples, web_read_mode=args.read_mode)
    except (ValueError, TypeError, OSError, DataAdapterError): parser.error('Invalid timeout, provider or sample options')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'ok' else 1


if __name__ == '__main__': raise SystemExit(main())
