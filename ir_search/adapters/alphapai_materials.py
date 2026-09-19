"""Bounded AlphaPai shared-meeting discovery; analysis remains with calling skills."""
from __future__ import annotations

from dataclasses import replace

from ir_search.context import RequestStopped
from ir_search.contracts import AdapterMode, Diagnostic, Provenance
from ir_search.contracts.materials import MaterialCandidate, MaterialCapability, MaterialKind, MaterialSearchPage, MaterialSourceScan, TextScope
from ir_search.infrastructure.alphapai import AlphapaiClient, _reference
from ir_search.infrastructure.alphapai_documents import fetch_alphapai_document, _metadata, _plain, CST
from ir_search.models import EvidenceType, SourceAuthority, SourceTier, FailureKind
from ir_search.registry import DataAdapterError


def _diag(code, failure=FailureKind.NONE):
    return Diagnostic(code, 'search_materials', 'alphapai', failure_kind=failure, adapter_mode=AdapterMode.LIVE)


class AlphapaiMaterialAdapter:
    name = 'alphapai'

    def __init__(self, profile, *, client=None):
        self._profile, self._client = profile, client
        self.capability = MaterialCapability('alphapai', 'research_platform',
            (MaterialKind.CALL_TRANSCRIPT, MaterialKind.CHANNEL_CHECK), max_symbols=5, allows_stored_summaries=True,
            search_basis='bounded_provider_keyword_search_local_matching',
            coverage_notes=('account_login_optional_browser_dependency', 'shared_meetings_only',
                'stored_ai_summaries_not_verbatim_original', 'transcripts_may_be_partial',
                'opaque_reference_stability_unverified', 'bounded_pages_not_exhaustive',
                'no_open_api_key_required', 'no_qa_or_generation_calls', 'personal_recordings_not_enabled'))

    def search_materials(self, request, *, context):
        """Search explicit publication windows and read only a bounded detail selection."""
        context.check_active()
        if context.account_scope != 'default': raise DataAdapterError('entitlement_denied')
        if not request.published_start or not request.published_end: raise ValueError('Publication window required')
        if len(request.symbols) > 5: raise DataAdapterError('unsupported')
        terms = request.entities or request.keywords or (request.question,)
        query = terms[0]
        if len(query)>200: raise ValueError('Use a short explicit keyword')
        page = MaterialSearchPage([], 0)
        page.diagnostics.append(_diag('alphapai_web_account_stored_materials'))
        if len(terms)>1: page.diagnostics.append(_diag('alphapai_single_query_remaining_terms_local_only'))
        client = self._client if self._client is not None else AlphapaiClient(self._profile)
        seen = set(); fingerprints = set(); stop = False
        try:
            # Fixed page size prevents skipped rows when the last local allowance shrinks.
            size = min(20, request.candidates_per_source)
            for number in range(1, self._profile.max_pages+1):
                allowance = request.candidates_per_source-page.scanned_count
                if allowance <= 0: break
                scan_count = len(page.scans)
                try:
                    reply = client.read('list', {'query':query, 'start':request.published_start.isoformat(),
                        'end':request.published_end.isoformat(), 'page':number, 'size':size, 'symbols':request.symbols}, context=context)
                    rows, total = reply.data['list'], reply.data['total']
                    if not isinstance(rows,list) or len(rows)>size or type(total) is not int or total<0: raise DataAdapterError('upstream_schema')
                    inspected = rows[:allowance]; page.scanned_count += len(inspected)
                    more = len(rows)>len(inspected) or number*size<total
                    page.scans.append(MaterialSourceScan('meeting_list', MaterialKind.CALL_TRANSCRIPT,
                        request.published_start, request.published_end, 'queried', len(rows), len(inspected),
                        symbols=request.symbols, collection_id='shared_meetings', has_more=more,
                        discovery_provider='alphapai', search_query=query, fetched_at=reply.fetched_at,
                        date_filter_basis='local_publication_metadata'))
                    fresh = 0
                    for row in inspected:
                        identifier, title, publisher, published, meeting, symbols = _metadata(row)
                        if published is None or not request.published_start <= published.astimezone(CST).date() <= request.published_end:
                            page.diagnostics.append(_diag('alphapai_publication_unknown_or_outside_window')); continue
                        if identifier in seen:
                            page.diagnostics.append(_diag('alphapai_duplicate_reference')); continue
                        fingerprint = (title, publisher, published, meeting, symbols)
                        if fingerprint in fingerprints:
                            # Could be ID rotation or a distinct identically labelled meeting: do not silently merge.
                            page.diagnostics.append(_diag('alphapai_ambiguous_repeated_metadata')); continue
                        seen.add(identifier); fingerprints.add(fingerprint); fresh += 1
                        kind = MaterialKind.CHANNEL_CHECK if any(t in title for t in ('经销商','渠道','动销')) else MaterialKind.CALL_TRANSCRIPT
                        if request.material_types and kind not in request.material_types: continue
                        text = _plain(row.get('content') or row.get('aiContent'), request.max_chars)
                        warnings = ('alphapai_list_snippet_not_full_text','alphapai_reference_stability_unverified',
                            'alphapai_time_assumed_asia_shanghai', 'alphapai_source_claims_not_independently_verified')
                        page.candidates.append(MaterialCandidate(_reference(identifier), title, kind, 'research_platform',
                            Provenance('alphapai', publisher, reply.fetched_at, authority=SourceAuthority.DATA_VENDOR,
                                source_tier=SourceTier.MEDIA,
                                evidence_type=EvidenceType.OPINION, adapter_mode=AdapterMode.LIVE),
                            text=text, text_scope=TextScope.SEARCH_SNIPPET if text else TextScope.METADATA,
                            symbols=symbols, published_at=published, published_on=published.date(), warnings=warnings,
                            discovery_provider='alphapai', text_provider='alphapai' if text else None,
                            collection_id='shared_meetings', source_record_type='shared_meeting',
                            read_details={'content_origin':'provider_list_snippet', 'complete':False,
                                'source_text_trust':'untrusted', 'meeting_at':meeting.isoformat() if meeting else None,
                                'publisher_identity_verification':'provider_label_only',
                                'material_kind_basis':'title_heuristic_not_verified_genre'}))
                    if not more: break
                    if not rows or fresh == 0:
                        page.diagnostics.append(_diag('alphapai_pagination_stalled', FailureKind.UPSTREAM_SCHEMA)); break
                    if number == self._profile.max_pages or page.scanned_count>=request.candidates_per_source:
                        page.diagnostics.append(_diag('alphapai_scan_limit', FailureKind.BUDGET_EXHAUSTED))
                except (DataAdapterError, RequestStopped) as exc:
                    page.diagnostics.append(_diag(exc.code, exc.failure_kind)); stop = True
                    if len(page.scans) == scan_count:
                        page.scans.append(MaterialSourceScan('meeting_list', MaterialKind.CALL_TRANSCRIPT,
                            request.published_start, request.published_end, 'failed', discovery_provider='alphapai'))
                    break
            if stop: return page
            ranked = sorted(range(len(page.candidates)), key=lambda i: -sum(
                term.casefold() in (page.candidates[i].title+' '+page.candidates[i].text).casefold()
                for term in (request.keywords or terms)))
            for position, index in enumerate(ranked):
                candidate = page.candidates[index]
                if position >= request.text_reads_per_source:
                    page.candidates[index] = replace(candidate, warnings=candidate.warnings+('text_not_read_within_budget',)); continue
                try:
                    doc = fetch_alphapai_document(candidate.source_ref, context=context, max_chars=request.max_chars,
                        profile=self._profile, client=client)
                    if doc.title != candidate.title or doc.published_at != candidate.published_at:
                        raise DataAdapterError('upstream_schema')
                    page.candidates[index] = replace(candidate, text=doc.text, text_scope=TextScope.ABSTRACT,
                        provenance=replace(candidate.provenance, generated=True, fetched_at=doc.fetched_at),
                        warnings=tuple(dict.fromkeys(doc.warnings+['material_kind_title_heuristic'])),
                        text_provider='alphapai', read_details=doc.extra['material_read'])
                except (DataAdapterError, RequestStopped) as exc:
                    page.diagnostics.append(_diag(exc.code, exc.failure_kind))
                    page.candidates[index] = replace(candidate, warnings=candidate.warnings+('alphapai_detail_unavailable',))
                    if isinstance(exc, RequestStopped) or exc.code in {'quota','rate_limit','authentication_failed',
                            'entitlement_denied','alphapai_call_budget_exhausted','alphapai_login_challenge'}: break
            return page
        finally:
            if self._client is None: client.close()
