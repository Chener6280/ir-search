"""Bounded XHS search and optional note reads through the shared material service."""
from __future__ import annotations

from dataclasses import replace

from ir_search.context import RequestStopped
from ir_search.contracts import AdapterMode, Diagnostic, Provenance
from ir_search.contracts.materials import MaterialCandidate, MaterialCapability, MaterialKind, MaterialSearchPage, MaterialSection, MaterialSourceScan, TextScope
from ir_search.infrastructure.xhs import XhsClient, _id, _reference, _snapshot
from ir_search.infrastructure.xhs_documents import fetch_xhs_document, _author, _counts, _text
from ir_search.infrastructure.material_cursor import _states, _slice, _continuation
from ir_search.models import EvidenceType, SourceAuthority, SourceTier, FailureKind
from ir_search.registry import DataAdapterError


def _diag(code, failure=FailureKind.NONE):
    return Diagnostic(code,'search_materials','xhs',failure_kind=failure,adapter_mode=AdapterMode.LIVE)


class XhsMaterialAdapter:
    name='xhs'

    def __init__(self, profile, *, client=None):
        self._profile=profile; self._client=client
        self.capability=MaterialCapability('xhs','community',(MaterialKind.SOCIAL_POST,),
            supports_publication_filter=False,search_basis='bounded_platform_keyword_search',coverage_notes=(
                'optional_local_xiaohongshu_mcp_service_requires_user_login',
                'first_search_snapshot_only_no_exhaustive_platform_history',
                'continuation_reads_remaining_snapshot_not_upstream_page_two',
                'publication_dates_require_note_detail_and_are_filtered_locally',
                'ugc_opinions_not_verified_sales_or_channel_data',
                'no_llm_no_publishing_no_likes_no_cookie_discovery'))

    def search_materials(self, request, *, context):
        """Search one keyword expression and preserve per-note failures and provenance."""
        if context.account_scope!='default': raise DataAdapterError('entitlement_denied')
        if not request.published_start: raise ValueError('Publication bounds required')
        if request.material_types and MaterialKind.SOCIAL_POST not in request.material_types: raise DataAdapterError('unsupported')
        query=' '.join(dict.fromkeys(request.entities+request.keywords)) or request.question
        if len(query)>200: raise ValueError('Use a shorter XHS query')
        client=self._client or XhsClient(self._profile)
        page=MaterialSearchPage([],0)
        page.diagnostics.append(_diag('xhs_search_bounded_not_exhaustive'))
        states=_states(request,'xhs',context)
        if set(states)-{(request.xhs_sort,query)}: raise DataAdapterError('invalid_cursor')
        state=states.get((request.xhs_sort,query))
        reply=client.search(query,sort=request.xhs_sort,context=context,refresh=request.xhs_cache_mode=='refresh',
            snapshot=state['cursor'] if state else None)
        rows=reply.data['feeds']; selected,end=_slice(rows,request.candidates_per_source,state)
        offset=end-len(selected); consumed=offset
        seen=set(); reads=0
        for row in selected:
            try: context.check_active()
            except RequestStopped as exc:
                page.diagnostics.append(_diag(exc.code,exc.failure_kind)); break
            consumed+=1
            try:
                if row.get('modelType')!='note': continue
                identifier=_id(row.get('id'))
                # Deduplicate across the entire snapshot, including preceding slices.
                prior=rows[:offset]
                if identifier in seen or any(r.get('id')==identifier and r.get('modelType')=='note' for r in prior): continue
                seen.add(identifier)
                card=row.get('noteCard')
                if not isinstance(card,dict): raise DataAdapterError('upstream_schema')
                title=_text(card.get('displayTitle'),2000) or '小红书笔记 '+identifier
                warnings=('xhs_ugc_not_independently_verified','published_date_unknown','business_period_unknown')
                candidate=MaterialCandidate(_reference(identifier),title,MaterialKind.SOCIAL_POST,'community',
                    Provenance('xhs',_author(card.get('user')),reply.fetched_at,authority=SourceAuthority.UGC,
                        source_tier=SourceTier.UGC,evidence_type=EvidenceType.OPINION,adapter_mode=AdapterMode.LIVE),
                    original_url='https://www.xiaohongshu.com/explore/'+identifier,warnings=warnings,
                    authors=(_author(card.get('user')),),source_document_id=identifier,discovery_provider='xhs',
                    source_record_type='note',read_details={'complete':False,'backend':'xiaohongshu_mcp_http',
                        'interaction_counts':_counts(card),'interaction_counts_basis':'snapshot_not_growth_or_sales',
                        'snapshot_fetched_at':reply.fetched_at.isoformat(),'cache_state':reply.cache_state})
                if reads<request.text_reads_per_source:
                    reads+=1
                    try:
                        doc=fetch_xhs_document(candidate.source_ref,context=context,client=client,max_chars=request.max_chars,
                            comment_limit=request.xhs_comment_limit,cache_mode=request.xhs_cache_mode)
                        if doc.published_at and not request.published_start<=doc.published_at.date()<=request.published_end:
                            page.diagnostics.append(_diag('xhs_outside_publication_window')); continue
                        candidate=replace(candidate,title=doc.title,text=doc.text,text_scope=TextScope.EXTRACTED_TEXT,
                            published_at=doc.published_at,published_on=doc.published_at.date() if doc.published_at else None,
                            provenance=replace(candidate.provenance,fetched_at=doc.fetched_at,publisher=doc.extra['publisher']),
                            authors=(doc.extra['publisher'],),warnings=tuple(doc.warnings),text_provider='xiaohongshu_mcp',
                            read_details=doc.extra['material_read'],sections=tuple(MaterialSection(**s) for s in doc.extra['sections']))
                    except (DataAdapterError,RequestStopped) as exc:
                        page.diagnostics.append(_diag(exc.code,exc.failure_kind))
                        candidate=replace(candidate,warnings=warnings+('original_text_fetch_failed',),
                            read_details={**candidate.read_details,'read_failure':exc.code})
                        page.candidates.append(candidate)
                        if isinstance(exc,RequestStopped) or exc.code in {'xhs_login_required','authentication_failed','web_content_challenge','rate_limit','timeout','xhs_backend_unavailable'}: break
                        continue
                else: candidate=replace(candidate,warnings=warnings+('text_not_read_within_budget',))
                page.candidates.append(candidate)
            except DataAdapterError as exc: page.diagnostics.append(_diag(exc.code,exc.failure_kind))
        continuation=_continuation(request,'xhs',request.xhs_sort,query,_snapshot(reply),rows,consumed,'',None,context)
        if continuation: page.continuation_cursors.append(continuation)
        page.scanned_count=consumed-offset
        page.scans.append(MaterialSourceScan('keyword_search',MaterialKind.SOCIAL_POST,request.published_start,
            request.published_end,'queried',len(rows),page.scanned_count,date_filter_basis='local_publication_metadata',
            discovery_provider='xhs',search_query=query,fetched_at=reply.fetched_at,cache_state=reply.cache_state,
            has_more=True if continuation else None))
        return page
