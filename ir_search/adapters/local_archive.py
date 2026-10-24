"""Expose the opt-in local snapshot through the normal material contracts."""
from datetime import datetime, timezone
import os
import re

from ir_search.contracts import AdapterMode, Diagnostic, Material, Provenance
from ir_search.contracts.materials import MaterialCandidate, MaterialCapability, MaterialKind, MaterialSearchPage, TextScope
from ir_search.models import EvidenceType, SourceAuthority
from ir_search.services.local_archive import read_local_archive, search_local_archive


def _fetched(row):
    try:
        d = datetime.fromisoformat(str(row.get('fetched_at')).replace('Z', '+00:00'))
        if d.utcoffset() is not None:
            return d
    except ValueError:
        pass
    # This is a local read time, never a fabricated publication date.
    return datetime.now(timezone.utc)


class LocalArchiveAdapter:
    name = 'local_archive'

    def __init__(self, root):
        self.root = root
        self.capability = MaterialCapability(self.name, 'local_archive', (MaterialKind.DOCUMENT, MaterialKind.AUDIO),
            search_basis='literal_local_snapshot', allows_stored_summaries=True,
            coverage_notes=('local_snapshot_not_live_source', 'archive_coverage_not_proven',
                            'unknown_dates_retained_with_warning', 'no_automatic_network_calls'))

    def search_materials(self, request, *, context):
        context.begin_operation()
        # Search each explicit keyword, then deduplicate under the shared budget.
        terms = request.keywords or (request.question,)
        hits, total = {}, 0
        for term in terms:
            context.check_active()
            found = search_local_archive(self.root, query=term, start=request.published_start.isoformat(),
                end=request.published_end.isoformat(), limit=min(100, request.candidates_per_source), include_unknown_dates=True,
                kind='audio' if request.material_types == (MaterialKind.AUDIO,) else '')
            total += found['total']
            for r in found['items']:
                if len(hits) < request.candidates_per_source:
                    hits[r['id']] = r
        page = MaterialSearchPage([], len(hits), complete=False)
        for index, hit in enumerate(hits.values()):
            context.check_active()
            r = read_local_archive(self.root, hit['id'], max_chars=request.max_chars)
            text = r['text'] if index < request.text_reads_per_source else ''
            scope = TextScope(r['text_scope']) if text else TextScope.METADATA
            generated = scope == TextScope.ABSTRACT
            warnings = list(r['warnings'])
            if not r['published_on']:
                warnings.append('published_date_unknown')
            if r['has_more']:
                warnings.append('text_truncated')
            page.candidates.append(MaterialCandidate(r['local_ref'], r['title'], MaterialKind.AUDIO if r.get('media_kind')=='audio' else MaterialKind.DOCUMENT, 'local_archive',
                Provenance(self.name, r['source'], _fetched(r), authority=SourceAuthority.UNKNOWN,
                           evidence_type=EvidenceType.OPINION if generated else EvidenceType.UNKNOWN,
                           generated=generated, adapter_mode=AdapterMode.LIVE),
                text=text, text_scope=scope, published_on=r['published_on'], original_url=r['original_url'],
                collection_id=r['collection_id'] or None, discovery_provider=self.name,
                text_provider=self.name if text else None, warnings=tuple(dict.fromkeys(warnings)),
                read_details={'archive_source': r['source'], 'original_source_ref': r['source_ref'],
                              'parse_status': r['parse_status'], 'date_basis': r['date_basis'],
                              'content_origin': 'provider_stored_summary' if generated else r.get('content_origin', 'local_snapshot'),
                              'machine_transcribed': r.get('machine_transcribed', False),
                              'whole_audio_transcribed': r.get('whole_audio_transcribed'),
                              'parser': r.get('parser'), 'text_hash': r['text_hash']}))
        page.diagnostics.append(Diagnostic('local_snapshot_not_live_source', 'search_materials', self.name,
                                           adapter_mode=AdapterMode.LIVE))
        if total > len(hits):
            page.diagnostics.append(Diagnostic('local_archive_candidate_budget', 'search_materials', self.name))
        return page


def retrieve_local_material(url, request, context):
    match = re.fullmatch(r'localarchive://document/([0-9a-f]{64})', url)
    if not match:
        raise ValueError('invalid_local_archive_reference')
    root = os.environ.get('IR_SEARCH_LOCAL_ARCHIVE_ROOT')
    if not root:
        raise ValueError('local_archive_not_configured')
    context.begin_operation()
    r = read_local_archive(root, match[1], max_chars=request.max_chars)
    generated = r['text_scope'] == 'abstract'
    text = r['text']
    spans = []
    # Exact offsets in returned text, not LLM-written quotations.
    terms = re.findall(r'[\u4e00-\u9fff]{2,20}|[A-Za-z0-9_-]{2,30}', request.question)
    for term in terms:
        for m in re.finditer(re.escape(term), text, re.I):
            start, end = max(0,m.start()-60), min(len(text),m.end()+140)
            page = next((p['page'] for p in r.get('pages', []) if p['start'] <= m.start() < p['end']), None)
            span = {'text': text[start:end], 'start_char': start, 'end_char': end, 'page': page,
                    'source_ref': url, 'text_hash': r['text_hash'], 'offset_unit': 'unicode_code_points'}
            if r.get('machine_transcribed'):
                matching = [s for s in r.get('transcript_segments', []) if s['start_char'] < end and s['end_char'] > start]
                if matching:
                    span['extra'] = {'start_ms': min(s['start_ms'] for s in matching),
                        'end_ms': max(s['end_ms'] for s in matching), 'machine_transcribed': True,
                        'time_basis': 'asr_utterance_offsets_not_word_alignment', 'timestamp_navigation_supported': False}
            spans.append(span)
            if len(spans) >= request.max_spans:
                break
        if len(spans) >= request.max_spans:
            break
    return Material(r['id'], url, r['title'], text,
        Provenance('local_archive', r['source'], _fetched(r), authority=SourceAuthority.UNKNOWN,
                   evidence_type=EvidenceType.OPINION if generated else EvidenceType.UNKNOWN,
                   adapter_mode=AdapterMode.LIVE, generated=generated),
        published_at=None, extraction_method=r.get('parser') or 'stored_archive_text',
        text_origin='provider_summary' if generated else 'asr_transcript' if r.get('machine_transcribed') else r['text_scope'], evidence_spans=spans,
        warnings=list(dict.fromkeys(r['warnings'] + (['text_truncated'] if r['has_more'] else []))),
        original_url=r['original_url'], text_provider='local_archive', text_hash=r['text_hash'],
        read_details={'published_on': r['published_on'], 'date_basis': r['date_basis'],
                      'archive_source': r['source'], 'original_source_ref': r['source_ref'],
                      'parse_status': r['parse_status'], 'network_calls': 0,
                      'machine_transcribed': r.get('machine_transcribed', False),
                      'whole_audio_transcribed': r.get('whole_audio_transcribed'),
                      'duration_seconds': r.get('duration_seconds'), 'completed_seconds': r.get('completed_seconds'),
                      'transcript_segments': r.get('transcript_segments', [])})
