"""Account-scoped WeChat research materials through the verified vendor API."""
from __future__ import annotations

from dataclasses import replace
import math
from urllib.parse import urlsplit

from ir_search.context import RequestStopped
from ir_search.contracts import AdapterMode, Diagnostic, Provenance
from ir_search.contracts.materials import (MaterialCandidate, MaterialCapability, MaterialKind,
    MaterialSearchPage, MaterialSourceScan, TextScope)
from ir_search.infrastructure.wechat import DajialaMaterialClient, fetch_wechat_document, normalize_wechat_url, _published
from ir_search.models import EvidenceType, FailureKind, SourceAuthority, SourceTier
from ir_search.registry import DataAdapterError
from ir_search.infrastructure.material_cursor import _states, _slice, _continuation
from ir_search.infrastructure.recovery import _stop_source


def _diag(code, failure=FailureKind.NONE):
    return Diagnostic(code,'search_materials','wechat',failure_kind=failure,adapter_mode=AdapterMode.LIVE)


class WechatMaterialAdapter:
    name = 'wechat'

    def __init__(self, profile, *, client=None, reader=None):
        self._profile = profile
        self._client = client or DajialaMaterialClient(profile)
        self._reader = reader or fetch_wechat_document
        self.capability = MaterialCapability(self.name,'wechat',(MaterialKind.WEB_PAGE,),
            supports_publication_filter=False, search_basis='bounded_account_timelines_local_matching',
            coverage_notes=('configured_accounts_only','not_global_wechat_search','publication_filter_applied_locally',
                            'vendor_discovery_not_official_wechat_api','publisher_identity_not_independently_verified',
                            'body_read_budget_may_miss_keyword_matches','images_and_media_not_transcribed'))

    def search_materials(self, request, *, context):
        """Scan bounded account timelines and read a bounded number of article bodies."""
        context.check_active()
        client = (DajialaMaterialClient(self._profile, transport=self._client._transport,
                    cache=self._client._cache, cache_mode=request.wechat_cache_mode)
                  if isinstance(self._client, DajialaMaterialClient) else self._client)
        if not request.published_start or not request.published_end: raise ValueError('Publication window required')
        if request.wechat_accounts:
            accounts=[]
            for selected in request.wechat_accounts:
                matches=[a for a in self._profile.accounts if selected in (a.name,a.ghid)]
                if len(matches)!=1: raise DataAdapterError('wechat_account_unresolved')
                if matches[0] not in accounts: accounts.append(matches[0])
        else: accounts=list(self._profile.accounts)
        states = _states(request, self.name, context)
        if states:
            if any(q != '' for _,q in states) or not {n for n,_ in states} <= {a.name for a in accounts}:
                raise DataAdapterError('invalid_cursor')
            accounts=[a for a in accounts if (a.name,'') in states]
        page=MaterialSearchPage([],0)
        if len(accounts)>self._profile.max_accounts_per_query:
            page.diagnostics.append(_diag('wechat_account_budget_exhausted',FailureKind.BUDGET_EXHAUSTED))
        accounts=accounts[:self._profile.max_accounts_per_query]
        seen=set()
        for index, account in enumerate(accounts):
            remaining=request.candidates_per_source-page.scanned_count
            if remaining<=0:
                page.diagnostics.append(_diag('candidate_budget_exhausted',FailureKind.BUDGET_EXHAUSTED)); break
            allowance=math.ceil(remaining/(len(accounts)-index))
            state=states.get((account.name,''))
            cursor=state['cursor'] if state else ''; seen_cursors=set(); used=0; continuation=None
            for _ in range(self._profile.max_pages_per_account):
                if used>=allowance: break
                try:
                    history=client.history(account,cursor=cursor,context=context)
                except (DataAdapterError,RequestStopped) as exc:
                    page.diagnostics.append(_diag(exc.code,exc.failure_kind))
                    page.scans.append(MaterialSourceScan('post_history',MaterialKind.WEB_PAGE,request.published_start,
                        request.published_end,'failed',publisher_filter=account.name,collection_id=account.ghid or None,
                        date_filter_basis='local_publication_metadata',discovery_provider='dajiala'))
                    if isinstance(exc,RequestStopped) or _stop_source(exc.code):
                        if continuation: page.continuation_cursors.append(continuation)
                        return page
                    break
                try:
                    rows, position = _slice(history.rows, allowance-used, state)
                    continuation = _continuation(request, self.name, account.name, '', cursor, history.rows,
                        position, history.next_cursor if history.next_cursor not in seen_cursors else '', history.has_more, context)
                    state = None
                except DataAdapterError as exc:
                    page.diagnostics.append(_diag(exc.code,exc.failure_kind));break
                page.diagnostics.extend(_diag(w) for w in history.warnings)
                used+=len(rows); page.scanned_count+=len(rows)
                page.scans.append(MaterialSourceScan('post_history',MaterialKind.WEB_PAGE,request.published_start,
                    request.published_end,'queried',len(history.rows),len(rows),publisher_filter=history.publisher,
                    collection_id=history.ghid,date_filter_basis='local_publication_metadata',has_more=history.has_more,
                    next_cursor=history.next_cursor,discovery_provider='dajiala',
                    fetched_at=history.fetched_at,cache_state=history.cache_state))
                for row in rows:
                    try:
                        url=normalize_wechat_url(row.get('url'))
                        if url in seen: continue
                        seen.add(url)
                        title=row.get('title')
                        if not isinstance(title,str) or not title.strip() or len(title)>1000: raise DataAdapterError('upstream_schema')
                        published=_published(row)
                        if published and not request.published_start<=published.date()<=request.published_end: continue
                        digest=row.get('digest') or ''
                        if not isinstance(digest,str): raise DataAdapterError('upstream_schema')
                        warnings=['publisher_identity_not_independently_verified','publication_from_vendor_metadata']
                        warnings.extend(history.warnings)
                        if row.get('original') == 2: warnings.append('vendor_reports_repost')
                        elif row.get('original') == 1: warnings.append('vendor_reports_original_claim_unverified')
                        if not published: warnings.append('published_date_unknown')
                        if history.publisher!=account.name: warnings.append('configured_account_name_changed')
                        if len(digest)>request.max_chars: warnings.append('text_truncated')
                        text=digest.strip()[:request.max_chars]
                        page.candidates.append(MaterialCandidate(url,title,MaterialKind.WEB_PAGE,'wechat',
                            Provenance('wechat',history.publisher,history.fetched_at,source_tier=SourceTier.MEDIA,
                                authority=SourceAuthority.UNKNOWN,evidence_type=EvidenceType.UNKNOWN,adapter_mode=AdapterMode.LIVE),
                            text=text,text_scope=TextScope.ABSTRACT if text.strip() else TextScope.METADATA,
                            original_url=url,published_on=published.date() if published else None,published_at=published,
                            warnings=tuple(warnings),discovery_provider='dajiala',text_provider='dajiala' if text.strip() else None,
                            collection_id=history.ghid,collection_name=history.publisher,source_record_type='wechat_article',
                            source_document_id=url))
                    except (DataAdapterError,ValueError,TypeError) as exc:
                        page.diagnostics.append(_diag(exc.code if isinstance(exc,DataAdapterError) else 'invalid_wechat_record',FailureKind.UPSTREAM_SCHEMA))
                if position<len(history.rows): page.diagnostics.append(_diag('candidate_budget_exhausted',FailureKind.BUDGET_EXHAUSTED))
                if position < len(history.rows): break
                if not history.has_more: break
                if not history.next_cursor or history.next_cursor==cursor or history.next_cursor in seen_cursors:
                    page.diagnostics.append(_diag('wechat_pagination_stalled',FailureKind.UPSTREAM_SCHEMA)); break
                seen_cursors.add(history.next_cursor); cursor=history.next_cursor
            else:
                page.diagnostics.append(_diag('wechat_page_budget_exhausted',FailureKind.BUDGET_EXHAUSTED))
            if continuation: page.continuation_cursors.append(continuation)
        terms=request.keywords+request.entities
        order=sorted(range(len(page.candidates)),key=lambda i: -sum(t.casefold() in (page.candidates[i].title+' '+page.candidates[i].text).casefold() for t in terms))
        reads=set(order[:request.text_reads_per_source])
        for i in order:
            candidate=page.candidates[i]
            if i not in reads:
                page.candidates[i]=replace(candidate,warnings=candidate.warnings+('text_not_read_within_budget',)); continue
            try:
                options = {'mode': request.web_read_mode, 'cache_mode': request.wechat_cache_mode} if self._reader is fetch_wechat_document else {}
                document=self._reader(candidate.original_url,context=context,max_chars=request.max_chars,client=client,**options)
                if (document.errors or not document.text.strip() or document.extra.get('adapter_mode')!='live'
                        or document.extra.get('generated') or document.extra.get('text_provider') not in {'wechat_origin','wechat_browser','dajiala'}):
                    raise DataAdapterError('upstream_schema')
                actual=normalize_wechat_url(document.url)
                if actual!=candidate.original_url and urlsplit(candidate.original_url).path == '/s':
                    raise DataAdapterError('wechat_article_mismatch')
                warnings=list(candidate.warnings)+document.warnings
                if document.extra.get('publisher') not in (None, 'unknown', candidate.collection_name):
                    warnings.append('publisher_metadata_conflict')
                if actual != candidate.original_url: warnings.append('short_url_resolved_by_text_provider')
                published=document.published_at or candidate.published_at
                if document.published_at and candidate.published_at and document.published_at!=candidate.published_at:
                    warnings.append('publication_date_conflict')
                if document.published_at: warnings=[w for w in warnings if w!='published_date_unknown']
                if published and not request.published_start<=published.date()<=request.published_end:
                    page.candidates[i]=None
                    page.diagnostics.append(_diag('wechat_outside_publication_window'));continue
                if len(document.text)>request.max_chars:warnings.append('text_truncated')
                page.candidates[i]=replace(candidate,text=document.text[:request.max_chars],text_scope=TextScope.EXTRACTED_TEXT,
                    original_url=actual,source_document_id=actual,
                    text_provider=document.extra['text_provider'],published_at=published,published_on=published.date() if published else None,
                    authors=tuple(document.extra.get('authors',[])),warnings=tuple(dict.fromkeys(warnings)),
                    read_details=document.extra.get('web_read', {}),
                    article=document.extra.get('article', {}), links=tuple(document.extra.get('public_links', [])),
                    provenance=replace(candidate.provenance,fetched_at=document.fetched_at))
            except (DataAdapterError,RequestStopped) as exc:
                page.diagnostics.append(_diag(exc.code,exc.failure_kind))
                page.candidates[i]=replace(candidate,warnings=candidate.warnings+('original_text_fetch_failed',))
                if isinstance(exc,RequestStopped) or _stop_source(exc.code): break
        page.candidates=[c for c in page.candidates if c is not None]
        page.diagnostics=list(dict.fromkeys(page.diagnostics))
        return page
