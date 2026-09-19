"""Bounded Gangtise stored-material search; publication windows are filtered locally."""
from __future__ import annotations

from dataclasses import replace

from ir_search.context import RequestStopped
from ir_search.contracts import AdapterMode, Diagnostic, Provenance
from ir_search.contracts.materials import MaterialCandidate, MaterialCapability, MaterialKind, MaterialSearchPage, MaterialSourceScan, TextScope
from ir_search.infrastructure.gangtise import GangtiseClient, _reference
from ir_search.infrastructure.gangtise_documents import fetch_gangtise_document, _metadata, _snippet, CST
from ir_search.models import EvidenceType, SourceAuthority, SourceTier, FailureKind
from ir_search.registry import DataAdapterError

_KINDS = {'summary': MaterialKind.CALL_TRANSCRIPT, 'report': MaterialKind.RESEARCH_REPORT, 'opinion': MaterialKind.OPINION}
_STOP = {'authentication_failed', 'gangtise_login_challenge', 'gangtise_login_busy', 'gangtise_state_invalid',
         'gangtise_state_unavailable', 'quota', 'rate_limit', 'gangtise_call_budget_exhausted',
         'browser_dependency_missing', 'browser_unavailable', 'browser_failed'}


def _diag(code, failure=FailureKind.NONE):
    return Diagnostic(code, 'search_materials', 'gangtise', failure_kind=failure, adapter_mode=AdapterMode.LIVE)


class GangtiseMaterialAdapter:
    name = 'gangtise'

    def __init__(self, profile, *, client=None):
        self._profile, self._client = profile, client
        self.capability = MaterialCapability('gangtise', 'research_platform', tuple(_KINDS.values()),
            allows_stored_summaries=True, search_basis='bounded_provider_keyword_search_local_matching',
            coverage_notes=('account_login_may_require_human_device_verification',
                'stored_minutes_report_abstracts_and_opinions', 'publication_window_filtered_locally',
                'no_full_text_or_exhaustive_coverage_claim', 'symbols_matched_locally_not_sent_as_gts_codes',
                'optional_browser_for_authentication_only', 'no_generation_or_official_ak_sk_calls'))

    def search_materials(self, request, *, context):
        """Search selected categories under one shared scan/read budget with visible gaps."""
        context.check_active()
        if context.account_scope != 'default': raise DataAdapterError('entitlement_denied')
        if not request.published_start or not request.published_end: raise ValueError('Publication window required')
        terms = request.entities or request.keywords or (request.question,)
        query = terms[0]
        if len(query) > 200: raise ValueError('Use a short explicit keyword')
        categories = [c for c,k in _KINDS.items() if not request.material_types or k in request.material_types]
        page = MaterialSearchPage([], 0)
        if not categories: return page
        page.diagnostics.extend([_diag('gangtise_local_publication_filter_bounded_not_exhaustive'),
                                 _diag('gangtise_keyword_query_not_structured_symbol_filter')])
        if len(terms)>1: page.diagnostics.append(_diag('gangtise_single_query_remaining_terms_local_only'))
        client = self._client if self._client is not None else GangtiseClient(self._profile)
        seen = set(); terminal = False
        # Round-robin categories avoids using the entire budget on the first collection.
        size = min(20, max(1, request.candidates_per_source//len(categories)))
        active = set(categories)
        try:
            for number in range(1, self._profile.max_pages+1):
                for category in categories:
                    if category not in active: continue
                    allowance = request.candidates_per_source-page.scanned_count
                    if allowance <= 0: break
                    try:
                        reply = client.read('search', {'category':category, 'query':query,
                            'page':number, 'size':size}, context=context)
                    except (DataAdapterError, RequestStopped) as exc:
                        page.diagnostics.append(_diag(exc.code, exc.failure_kind))
                        page.scans.append(MaterialSourceScan(category, _KINDS[category], request.published_start,
                            request.published_end, 'failed', discovery_provider='gangtise'))
                        active.discard(category)
                        terminal = isinstance(exc, RequestStopped) or exc.code in _STOP
                        if terminal: break
                        continue
                    rows, total = reply.data['list'], reply.data['total']
                    inspected = rows[:allowance]; page.scanned_count += len(inspected)
                    more = len(rows)>len(inspected) or number*size < total
                    page.scans.append(MaterialSourceScan(category, _KINDS[category], request.published_start,
                        request.published_end, 'queried', len(rows), len(inspected), collection_id=category,
                        has_more=more, discovery_provider='gangtise', search_query=query,
                        fetched_at=reply.fetched_at, date_filter_basis='local_publication_metadata'))
                    fresh = 0
                    for row in inspected:
                        try:
                            identifier, title, publisher, published = _metadata(row, category)
                            reference = _reference(category, identifier)
                            if reference in seen: continue
                            seen.add(reference); fresh += 1
                            if published is None or not request.published_start <= published.astimezone(CST).date() <= request.published_end:
                                page.diagnostics.append(_diag('gangtise_publication_unknown_or_outside_window')); continue
                            text = _snippet(row, category, request.max_chars)
                            warnings = ('gangtise_list_snippet_not_full_text', 'gangtise_source_claims_not_independently_verified',
                                'gangtise_naive_time_assumed_asia_shanghai', 'gangtise_reference_stability_unverified')
                            page.candidates.append(MaterialCandidate(reference, title, _KINDS[category], 'research_platform',
                                Provenance('gangtise', publisher, reply.fetched_at, authority=SourceAuthority.DATA_VENDOR,
                                    source_tier=SourceTier.MEDIA, evidence_type=EvidenceType.OPINION, adapter_mode=AdapterMode.LIVE),
                                text=text, text_scope=TextScope.SEARCH_SNIPPET if text else TextScope.METADATA,
                                published_at=published, published_on=published.date(), warnings=warnings,
                                discovery_provider='gangtise', text_provider='gangtise' if text else None,
                                collection_id=category, source_record_type=category,
                                read_details={'content_origin':'provider_list_snippet', 'complete':False, 'source_text_trust':'untrusted'}))
                        except DataAdapterError as exc: page.diagnostics.append(_diag(exc.code, exc.failure_kind))
                    if not more: active.discard(category)
                    elif not rows or not fresh:
                        page.diagnostics.append(_diag('gangtise_pagination_stalled', FailureKind.UPSTREAM_SCHEMA)); active.discard(category)
                if terminal or not active or page.scanned_count >= request.candidates_per_source: break
            if active and not terminal: page.diagnostics.append(_diag('gangtise_scan_limit', FailureKind.BUDGET_EXHAUSTED))
            if terminal: return page
            ranking = sorted(range(len(page.candidates)), key=lambda i: -sum(
                t.casefold() in (page.candidates[i].title+' '+page.candidates[i].text).casefold() for t in (request.keywords or terms)))
            for pos, index in enumerate(ranking):
                candidate = page.candidates[index]
                if pos >= request.text_reads_per_source:
                    page.candidates[index] = replace(candidate, warnings=candidate.warnings+('text_not_read_within_budget',)); continue
                try:
                    doc = fetch_gangtise_document(candidate.source_ref, context=context, profile=self._profile,
                        client=client, max_chars=request.max_chars)
                    if doc.title != candidate.title or doc.published_at != candidate.published_at: raise DataAdapterError('upstream_schema')
                    page.candidates[index] = replace(candidate, text=doc.text,
                        text_scope=TextScope(doc.extra['material_read']['text_scope']),
                        provenance=replace(candidate.provenance, fetched_at=doc.fetched_at, generated=doc.extra['generated']),
                        warnings=tuple(doc.warnings), text_provider='gangtise', read_details=doc.extra['material_read'])
                except (DataAdapterError, RequestStopped) as exc:
                    page.diagnostics.append(_diag(exc.code, exc.failure_kind))
                    page.candidates[index] = replace(candidate, warnings=candidate.warnings+('gangtise_detail_unavailable',))
                    if isinstance(exc, RequestStopped) or exc.code in _STOP: break
            return page
        finally:
            if self._client is None: client.close()
