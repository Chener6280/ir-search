"""Deterministic, bounded IMA knowledge-base and personal-note discovery."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from html import unescape
import math
import re

from ir_search.context import RequestStopped
from ir_search.contracts import AdapterMode, Diagnostic, Provenance
from ir_search.contracts.materials import (MaterialCandidate, MaterialCapability, MaterialKind,
    MaterialSearchPage, MaterialSourceScan, TextScope)
from ir_search.infrastructure.ima import IMAClient, _id, _ref, _page
from ir_search.infrastructure.ima_documents import fetch_ima_document
from ir_search.models import EvidenceType, FailureKind, SourceAuthority, SourceTier
from ir_search.registry import DataAdapterError
from ir_search.infrastructure.material_cursor import _states, _slice, _continuation
from ir_search.infrastructure.recovery import _stop_source


def _diag(code, failure=FailureKind.NONE):
    return Diagnostic(code,'search_materials','ima',failure_kind=failure,adapter_mode=AdapterMode.LIVE)


def _plain(value):
    if not isinstance(value,str) or len(value)>100000: raise DataAdapterError('upstream_schema')
    # Only provider highlight tags are removed; source text never becomes instructions.
    return unescape(re.sub(r'</?em(?:\s[^>]*)?>','',value,flags=re.I)).strip()


def _time(value):
    if value in (None,0,'0',''): return None
    if isinstance(value,str) and value.isdigit(): value=int(value)
    if type(value) is not int or not 0<value<253402300800000: raise DataAdapterError('upstream_schema')
    try: return datetime.fromtimestamp(value/1000,tz=timezone.utc)
    except (ValueError,OverflowError,OSError): raise DataAdapterError('upstream_schema') from None


class IMAMaterialAdapter:
    name='ima'

    def __init__(self, profile, *, client=None, reader=None):
        self._profile=profile
        self._client=client if client is not None else IMAClient(profile)
        self._reader=reader or fetch_ima_document
        self.capability=MaterialCapability('ima','knowledge_base',(MaterialKind.DOCUMENT,MaterialKind.NOTE),
            supports_publication_filter=False,search_basis='bounded_provider_keyword_search_local_matching',
            coverage_notes=('bounded_knowledge_base_discovery','not_all_ima_content_searchable',
                'publication_dates_often_unknown','note_created_modified_not_publication_dates',
                'search_snippets_not_original_text','original_read_requires_separate_permission',
                'publisher_and_material_genre_unverified','not_ima_ai_chat','maximum_two_literal_queries'))

    def search_materials(self, request, *, context):
        """Search up to two literal queries, preserve permission and pagination gaps."""
        context.check_active()
        if context.account_scope!='default': raise DataAdapterError('entitlement_denied')
        if not request.published_start or not request.published_end: raise ValueError('Publication window required')
        page=MaterialSearchPage([],0)
        # Keywords are preferred; entity names are the next choice, then literal question.
        queries=tuple(dict.fromkeys(request.keywords or request.entities or (request.question,)))
        if len(queries)>2: page.diagnostics.append(_diag('ima_query_budget_exhausted',FailureKind.BUDGET_EXHAUSTED))
        queries=queries[:2]
        states = _states(request, self.name, context)
        collections=[]
        selected=request.ima_knowledge_base_ids or self._profile.knowledge_base_ids
        if request.ima_knowledge_base_ids and self._profile.knowledge_base_ids and not set(selected)<=set(self._profile.knowledge_base_ids):
            raise DataAdapterError('entitlement_denied')
        if states:
            resumed=tuple(dict.fromkeys(kid for kid,q in states if kid != 'notes'))
            if any(q not in queries for kid,q in states) or selected and not set(resumed)<=set(selected):
                raise DataAdapterError('invalid_cursor')
            if any(kid == 'notes' for kid,q in states) and not (request.ima_include_notes and self._profile.include_notes
                    and (not request.material_types or MaterialKind.NOTE in request.material_types)):
                raise DataAdapterError('invalid_cursor')
            selected=resumed
        if selected:
            collections=[(_id(v),None) for v in selected]
        elif not states:
            try:
                reply=self._client.read('search_knowledge_base',{'query':'','cursor':'','limit':20},context=context)
                rows,more,cursor=_page(reply.data,'info_list')
                for row in rows:
                    # Both the documented schema and the observed official schema are accepted.
                    kid=_id(row.get('kb_id',row.get('id')))
                    name=_plain(row.get('kb_name',row.get('name')))
                    if not name or len(name)>1000: raise DataAdapterError('upstream_schema')
                    if kid not in {v[0] for v in collections}: collections.append((kid,name))
                if more is not False: page.diagnostics.append(_diag('ima_directory_not_exhaustive'))
            except (DataAdapterError,RequestStopped) as exc:
                page.diagnostics.append(_diag(exc.code,exc.failure_kind))
                page.scans.append(self._scan(request,'search_knowledge_base',MaterialKind.DOCUMENT,'failed'))
                if isinstance(exc,RequestStopped) or _stop_source(exc.code): return page
        if len(collections)>self._profile.max_knowledge_bases:
            page.diagnostics.append(_diag('ima_knowledge_base_budget_exhausted',FailureKind.BUDGET_EXHAUSTED))
        collections=collections[:self._profile.max_knowledge_bases]
        scopes=[('media',kid,name) for kid,name in collections]
        if request.ima_include_notes and self._profile.include_notes and (not request.material_types or MaterialKind.NOTE in request.material_types):
            scopes.append(('note',None,None))
        if states:
            scopes=[s for s in scopes if any((s[1] or 'notes',q) in states for q in queries)]
        if not scopes:
            page.diagnostics.append(_diag('ima_no_searchable_scope'))
            return page
        seen=set()
        for index,(kind,kid,name) in enumerate(scopes):
            remaining=request.candidates_per_source-page.scanned_count
            if remaining<=0:
                page.diagnostics.append(_diag('candidate_budget_exhausted',FailureKind.BUDGET_EXHAUSTED));break
            allowance=math.ceil(remaining/(len(scopes)-index));used=0
            operation='search_note' if kind=='note' else 'search_knowledge'
            scan_kind=MaterialKind.NOTE if kind=='note' else MaterialKind.DOCUMENT
            for query in queries:
                if used>=allowance: break
                state=states.get((kid or 'notes',query))
                if states and state is None: continue
                cursor=state['cursor'] if state else ''; cursors=set()
                if kind=='note' and cursor and (not cursor.isascii() or not cursor.isdigit() or not 0 <= int(cursor) < 1000):
                    raise DataAdapterError('invalid_cursor')
                offset=int(cursor or '0') if kind=='note' else 0
                continuation=None
                for _ in range(self._profile.max_pages):
                    if used>=allowance: break
                    parameters=({'query':query,'knowledge_base_id':kid,'cursor':cursor} if kind=='media' else
                        {'search_type':1,'sort_type':0,'query_info':{'content':query},'start':offset,'end':min(1000,offset+20)})
                    try:
                        reply=self._client.read(operation,parameters,context=context)
                        rows,more,next_cursor=_page(reply.data,'info_list' if kind=='media' else 'search_note_infos')
                        inspected, position = _slice(rows, allowance-used, state)
                        next_position = (str(offset+len(rows)) if rows and offset+len(rows)<1000 else '') if kind=='note' else next_cursor
                        if next_position in cursors: next_position=''
                        continuation = _continuation(request, self.name, kid or 'notes', query, str(offset) if kind=='note' else cursor,
                            rows, position, next_position, more, context)
                        state=None
                        used+=len(inspected);page.scanned_count+=len(inspected)
                        page.scans.append(self._scan(request,operation,scan_kind,'queried',len(rows),len(inspected),kid,
                            more,next_cursor,query))
                        for row in inspected:
                            try:
                                candidate=self._candidate(row,kind,kid,name,reply.fetched_at,request.max_chars)
                                if candidate is None:
                                    page.diagnostics.append(_diag('ima_non_document_search_hit'));continue
                                if candidate.source_ref not in seen:
                                    seen.add(candidate.source_ref);page.candidates.append(candidate)
                            except (DataAdapterError,ValueError,TypeError):
                                page.diagnostics.append(_diag('invalid_ima_record',FailureKind.UPSTREAM_SCHEMA))
                        if position<len(rows):
                            page.diagnostics.append(_diag('candidate_budget_exhausted',FailureKind.BUDGET_EXHAUSTED));break
                        if more is False: break
                        if more is None:
                            page.diagnostics.append(_diag('ima_pagination_unknown'));break
                        if kind=='note':
                            if not rows: page.diagnostics.append(_diag('ima_pagination_stalled',FailureKind.UPSTREAM_SCHEMA));break
                            offset+=len(rows)
                            if offset>=1000: page.diagnostics.append(_diag('ima_note_offset_limit',FailureKind.BUDGET_EXHAUSTED));break
                        else:
                            if not next_cursor or next_cursor==cursor or next_cursor in cursors:
                                page.diagnostics.append(_diag('ima_pagination_stalled',FailureKind.UPSTREAM_SCHEMA));break
                            cursors.add(next_cursor);cursor=next_cursor
                    except (DataAdapterError,RequestStopped) as exc:
                        page.diagnostics.append(_diag(exc.code,exc.failure_kind))
                        page.scans.append(self._scan(request,operation,scan_kind,'failed',kid=kid,query=query))
                        if isinstance(exc,RequestStopped) or _stop_source(exc.code):
                            if continuation: page.continuation_cursors.append(continuation)
                            return page
                        break
                else: page.diagnostics.append(_diag('ima_page_budget_exhausted',FailureKind.BUDGET_EXHAUSTED))
                if continuation: page.continuation_cursors.append(continuation)
        order=sorted(range(len(page.candidates)),key=lambda i:-sum(t.casefold() in (page.candidates[i].title+' '+page.candidates[i].text).casefold() for t in queries))
        for rank,index in enumerate(order):
            c=page.candidates[index]
            if rank>=request.text_reads_per_source:
                page.candidates[index]=replace(c,warnings=c.warnings+('text_not_read_within_budget',));continue
            try:
                doc=self._reader(c.source_ref,profile=self._profile,client=self._client,context=context,max_chars=request.max_chars)
                if (doc.url!=c.source_ref or doc.errors or not doc.text.strip() or doc.extra.get('adapter_mode')!='live'
                        or doc.extra.get('generated')): raise DataAdapterError('upstream_schema')
                if doc.published_at and not request.published_start<=doc.published_at.date()<=request.published_end:
                    page.candidates[index]=None;page.diagnostics.append(_diag('ima_outside_publication_window'));continue
                warnings=[w for w in c.warnings if w not in {'not_full_text','text_truncated'}]+doc.warnings
                if doc.published_at: warnings=[w for w in warnings if w!='published_date_unknown']
                if len(doc.text)>request.max_chars: warnings.append('text_truncated')
                page.candidates[index]=replace(c,text=doc.text[:request.max_chars],text_scope=TextScope.EXTRACTED_TEXT,
                    original_url=doc.canonical_url,published_at=doc.published_at,published_on=doc.published_at.date() if doc.published_at else None,
                    text_provider=doc.extra.get('text_provider','ima'),warnings=tuple(dict.fromkeys(warnings)),
                    provenance=replace(c.provenance,fetched_at=doc.fetched_at))
            except (DataAdapterError,RequestStopped) as exc:
                page.diagnostics.append(_diag(exc.code,exc.failure_kind))
                page.candidates[index]=replace(c,warnings=c.warnings+('original_text_fetch_failed',))
                if isinstance(exc,RequestStopped) or _stop_source(exc.code): break
        page.candidates=[c for c in page.candidates if c is not None]
        page.diagnostics=list(dict.fromkeys(page.diagnostics))
        return page

    @staticmethod
    def _scan(request,operation,kind,state,received=0,inspected=0,kid=None,more=None,cursor=None,query=None):
        return MaterialSourceScan(operation,kind,request.published_start,request.published_end,state,received,inspected,
            date_filter_basis='local_publication_metadata',collection_id=kid,has_more=more,next_cursor=cursor,
            discovery_provider='ima',search_query=query)

    @staticmethod
    def _candidate(row,kind,kid,name,fetched,max_chars):
        if kind=='note':
            row=row.get('note_book_info')
            if not isinstance(row,dict): raise DataAdapterError('upstream_schema')
            identifier=_id(row.get('note_id'));text=_plain(row.get('summary') or '')
            scope=TextScope.ABSTRACT; record='ima_personal_note'; material=MaterialKind.NOTE
            created,updated=_time(row.get('create_time')),_time(row.get('modify_time'))
        else:
            if not row.get('media_id') and row.get('folder_id'): return None
            identifier=_id(row.get('media_id'));text=_plain(row.get('highlight_content') or '')
            scope=TextScope.SEARCH_SNIPPET;record='ima_knowledge_item';material=MaterialKind.DOCUMENT
            if row.get('media_type')==11:material=MaterialKind.NOTE
            created=updated=None
        title=_plain(row.get('title'))
        if not title or len(title)>1000: raise DataAdapterError('upstream_schema')
        warnings=['published_date_unknown','publisher_unknown','not_full_text']
        if kind=='note':warnings.append('note_created_modified_not_publication_dates')
        if len(text)>max_chars:warnings.append('text_truncated')
        return MaterialCandidate(_ref(kind,identifier),title,material,'knowledge_base',
            Provenance('ima','unknown',fetched,source_tier=SourceTier.UGC,authority=SourceAuthority.UNKNOWN,
                evidence_type=EvidenceType.UNKNOWN,adapter_mode=AdapterMode.LIVE),text=text[:max_chars],
            text_scope=scope if text else TextScope.METADATA,warnings=tuple(warnings),source_document_id=identifier,
            discovery_provider='ima',text_provider='ima' if text else None,collection_id=kid,collection_name=name,
            source_record_type=record,source_created_at=created,source_updated_at=updated)
