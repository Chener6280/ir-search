"""Deterministic discovery over Wisburg's stored research collections."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta, timezone
import math

from ir_search.context import RequestStopped
from ir_search.contracts import AdapterMode, Diagnostic, Provenance
from ir_search.contracts.materials import (MaterialCandidate, MaterialCapability, MaterialKind,
    MaterialSearchPage, MaterialSourceScan, TextScope)
from ir_search.infrastructure.wisburg import WisburgClient, LIST_TOOLS, _reference, _parse_list
from ir_search.infrastructure.wisburg_documents import fetch_wisburg_document, _details, _warnings
from ir_search.models import EvidenceType, FailureKind, SourceAuthority, SourceTier
from ir_search.registry import DataAdapterError

KINDS = {
    'ib':MaterialKind.RESEARCH_REPORT, 'company':MaterialKind.RESEARCH_REPORT,
    'am':MaterialKind.RESEARCH_REPORT, 'archive':MaterialKind.POLICY,
    'ec':MaterialKind.CALL_TRANSCRIPT, 'feed':MaterialKind.NEWS, 'market_daily':MaterialKind.NEWS,
    'article':MaterialKind.OPINION, 'mikko':MaterialKind.OPINION,
}
_CST = timezone(timedelta(hours=8))


def _diag(code, failure=FailureKind.NONE):
    return Diagnostic(code,'search_materials','wisburg',failure_kind=failure,adapter_mode=AdapterMode.LIVE)


def _kind(category):
    return category if category in {'article','mikko'} else 'report'


class WisburgMaterialAdapter:
    name = 'wisburg'

    def __init__(self, profile, *, client=None, reader=None):
        self._profile, self._client, self._reader = profile, client, reader or fetch_wisburg_document
        self.capability = MaterialCapability('wisburg','research_platform',tuple(dict.fromkeys(KINDS.values())),
            search_basis='bounded_provider_keyword_search_local_matching', allows_stored_summaries=True,
            coverage_notes=('nine_stored_text_collections','single_literal_query','bounded_pages_not_exhaustive',
                'provider_summaries_not_original_reports','summary_generation_conservatively_flagged',
                'source_categories_not_verified_document_genres','cst_publication_window_not_business_period',
                'article_and_mikko_text_available','standalone_chart_search_not_enabled','no_chat_or_generation_calls'))

    def search_materials(self, request, *, context):
        """Search selected collections with bounded reads and exact source-scope labels."""
        context.check_active()
        if context.account_scope!='default': raise DataAdapterError('entitlement_denied')
        if not request.published_start or not request.published_end: raise ValueError('Publication window required')
        client = self._client if self._client is not None else WisburgClient(self._profile)
        page = MaterialSearchPage([],0)
        categories = [c for c in (request.wisburg_categories or tuple(LIST_TOOLS))
                      if not request.material_types or KINDS[c] in request.material_types]
        if not categories:
            page.diagnostics.append(_diag('wisburg_no_matching_category')); return page
        terms = request.entities or request.keywords or (request.question,)
        query = terms[0]
        if len(query)>200 or any(ord(c)<32 for c in query): raise ValueError('Provide a short literal keyword')
        if len(terms)>1: page.diagnostics.append(_diag('wisburg_single_query_remaining_terms_local_only'))
        start = request.published_start.isoformat()+'T00:00:00+08:00'
        end = (request.published_end+timedelta(days=1)).isoformat()+'T00:00:00+08:00'
        seen = set()
        for index,category in enumerate(categories):
            remaining = request.candidates_per_source-page.scanned_count
            if remaining<=0 or len(page.scans)>=16:
                page.diagnostics.append(_diag('wisburg_scan_budget_exhausted',FailureKind.BUDGET_EXHAUSTED));break
            allowance = math.ceil(remaining/(len(categories)-index))
            used, after, cursors = 0, None, set()
            for page_index in range(self._profile.max_pages_per_category):
                if used>=allowance or len(page.scans)>=16: break
                first = min(20,allowance-used)
                args = {'first':first,'query':query,'startTime':start,'endTime':end}
                if after: args['after']=after
                try:
                    reply = client.read(LIST_TOOLS[category],args,context=context)
                    rows, more, cursor = _parse_list(reply.text,category,first=first)
                    page.scanned_count+=len(rows); used+=len(rows)
                    page.scans.append(self._scan(request,category,'queried',len(rows),more,cursor,query,reply.fetched_at))
                    for row in rows:
                        if not request.published_start <= row.published_at.astimezone(_CST).date() <= request.published_end:
                            page.diagnostics.append(_diag('wisburg_outside_publication_window'));continue
                        reference = _reference(_kind(category),row.identifier)
                        if reference in seen:
                            page.diagnostics.append(_diag('wisburg_duplicate_report_id'));continue
                        seen.add(reference)
                        page.candidates.append(self._candidate(row,category,reply.fetched_at,request.max_chars))
                    if more is False: break
                    if more is None:
                        page.diagnostics.append(_diag('wisburg_pagination_unknown'));break
                    if not rows or cursor==after or cursor in cursors:
                        page.diagnostics.append(_diag('wisburg_pagination_stalled',FailureKind.UPSTREAM_SCHEMA));break
                    cursors.add(cursor);after=cursor
                    if used>=allowance or page_index+1>=self._profile.max_pages_per_category:
                        page.diagnostics.append(_diag('wisburg_page_or_candidate_limit',FailureKind.BUDGET_EXHAUSTED))
                except (DataAdapterError,RequestStopped) as exc:
                    page.diagnostics.append(_diag(exc.code,exc.failure_kind))
                    page.scans.append(self._scan(request,category,'failed',0,None,None,query,None))
                    if isinstance(exc,RequestStopped) or exc.code in {'authentication_failed','entitlement_denied','quota','rate_limit','wisburg_call_budget_exhausted'}:
                        return page
                    break
        # Read only a bounded selection. Title hits sort first; categories retain stable order.
        ranked = sorted(range(len(page.candidates)),key=lambda i:-sum(
            term.casefold() in (page.candidates[i].title+' '+page.candidates[i].text).casefold()
            for term in (request.keywords or terms)))
        reads = 0
        for index in ranked:
            candidate = page.candidates[index]
            if candidate.text_scope == TextScope.EXTRACTED_TEXT: continue
            if reads >= request.text_reads_per_source:
                page.candidates[index]=replace(candidate,warnings=candidate.warnings+('text_not_read_within_budget',));continue
            reads+=1
            try:
                doc=self._reader(candidate.source_ref,context=context,max_chars=request.max_chars,profile=self._profile,client=client)
                if doc.url!=candidate.source_ref or doc.errors or not doc.text.strip() or doc.extra.get('adapter_mode')!='live':
                    raise DataAdapterError('upstream_schema')
                if not doc.published_at or not request.published_start<=doc.published_at.astimezone(_CST).date()<=request.published_end:
                    raise DataAdapterError('upstream_schema')
                summary=doc.extra.get('generated',False)
                warnings=tuple(dict.fromkeys(doc.warnings+['source_category_not_verified_document_genre']))
                page.candidates[index]=replace(candidate,title=doc.title,text=doc.text[:request.max_chars],
                    text_scope=TextScope.ABSTRACT if summary else TextScope.EXTRACTED_TEXT,
                    published_at=doc.published_at,published_on=doc.published_at.date(),warnings=warnings,
                    provenance=replace(candidate.provenance,fetched_at=doc.fetched_at,generated=summary),
                    text_provider='wisburg',read_details=doc.extra.get('material_read',{}))
            except (DataAdapterError,RequestStopped) as exc:
                page.diagnostics.append(_diag(exc.code,exc.failure_kind))
                page.candidates[index]=replace(candidate,warnings=candidate.warnings+('wisburg_detail_fetch_failed',))
                if isinstance(exc,RequestStopped) or exc.code in {'authentication_failed','entitlement_denied','quota','rate_limit','wisburg_call_budget_exhausted'}: break
        page.diagnostics=list(dict.fromkeys(page.diagnostics))
        return page

    @staticmethod
    def _scan(request,category,state,count,more,cursor,query,fetched):
        return MaterialSourceScan(LIST_TOOLS[category].replace('-','_'),KINDS[category],request.published_start,
            request.published_end,state,count,count,has_more=more,next_cursor=cursor,discovery_provider='wisburg',
            collection_id=category,search_query=query,fetched_at=fetched)

    @staticmethod
    def _candidate(row,category,fetched,max_chars):
        kind=_kind(category);summary=kind=='report' and bool(row.text)
        warnings=_warnings(kind)+['source_category_not_verified_document_genre']
        if category=='feed' and row.text: warnings+=['provider_list_text_may_be_truncated','not_full_text']
        if len(row.text)>max_chars: warnings.append('text_truncated')
        scope = ((TextScope.ABSTRACT if kind=='report' else TextScope.EXTRACTED_TEXT if category=='mikko'
                  else TextScope.SEARCH_SNIPPET) if row.text else TextScope.METADATA)
        details = _details(kind)
        details['text_scope'] = scope.value
        if scope in {TextScope.METADATA, TextScope.SEARCH_SNIPPET}:
            details.update(content_origin='provider_metadata' if scope==TextScope.METADATA else 'provider_list_snippet',
                           generation_basis='not_asserted')
        return MaterialCandidate(_reference(kind,row.identifier),row.title,KINDS[category],'research_platform',
            Provenance('wisburg','智堡 Wisburg',fetched,authority=SourceAuthority.DATA_VENDOR,source_tier=SourceTier.MEDIA,
                evidence_type=EvidenceType.OPINION,adapter_mode=AdapterMode.LIVE,generated=summary),
            text=row.text[:max_chars],text_scope=scope,
            published_at=row.published_at,published_on=row.published_at.date(),warnings=tuple(warnings),
            authors=('Mikko',) if category=='mikko' else (),source_document_id=str(row.identifier),
            discovery_provider='wisburg',text_provider='wisburg' if row.text else None,
            collection_id=category,source_record_type='wisburg_'+category,read_details=details)
