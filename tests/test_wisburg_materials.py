"""Synthetic Wisburg contracts, list framing, stored summaries and SDK/MCP routing."""
from dataclasses import replace
from datetime import datetime, timezone
import json
import os

import pytest

from ir_search import MaterialRequest, MaterialSearchRequest, RequestContext, retrieve, search_materials
from ir_search.material_registry import MaterialRegistry, build_material_registry, list_material_capabilities
from ir_search.infrastructure.credentials import WisburgProfile, SourceConfigError, wisburg_profile, source_configuration_status
from ir_search.infrastructure.wisburg import (WisburgClient, WisburgResponse, _RPCResponse, LIST_TOOLS,
    _parse_list, _parse_detail, _parse_reference, _reference)
from ir_search.infrastructure.wisburg_documents import fetch_wisburg_document
from ir_search.adapters.wisburg_materials import WisburgMaterialAdapter
from ir_search.contracts.materials import TextScope
from ir_search.models import EvidenceType
from ir_search.registry import DataAdapterError
from ir_search import mcp_server

NOW=datetime(2026,9,17,9,tzinfo=timezone.utc)
DATE='2026-09-17T08:30:00+08:00'
PROFILE=WisburgProfile('synthetic_wisburg_key')
FOOTER='\n\n--- Page Info ---\nNext cursor: 2\nUse "after" parameter with this value to get the next page.'


def listing(identifier=101,title='美联储观点',body='',category='ib',cursor=True):
    head='Found 1 '+('Mikko logs' if category=='mikko' else 'articles' if category=='article' else 'feed items' if category=='feed' else 'reports')+':\n\n'
    row=f'[{identifier}] {DATE}\n{body}' if category=='mikko' else f'[{identifier}] {title}\n  date: {DATE}\n'+body
    return head+row+(FOOTER if cursor else '')


def detail(kind='report',identifier=101,body='美联储政策研究，仅为供应商整理内容。'):
    return ('' if kind=='mikko' else '# 美联储观点\n\n')+f'- ID: {identifier}\n- Date: {DATE}\n\n## '+('Summary' if kind=='report' else 'Content')+'\n\n'+body


class Client:
    def __init__(self, hook=None): self.calls=[];self.hook=hook
    def read(self,tool,args,*,context):
        context.begin_operation();self.calls.append((tool,args))
        if self.hook:
            result=self.hook(tool,args)
            if isinstance(result,Exception):raise result
            if result is not None:return WisburgResponse(result,NOW)
        if tool.startswith('list-'):
            cat=next(k for k,v in LIST_TOOLS.items() if v==tool)
            return WisburgResponse(listing(category=cat,body='美联储短评' if cat in {'mikko','feed'} else ''),NOW)
        return WisburgResponse(detail('article' if tool=='get-article-detail' else 'mikko' if tool=='get-mikko-log-detail' else 'report',args['id']),NOW)


def request(**changes):
    args=dict(question='美联储',keywords=('美联储',),providers=('wisburg',),wisburg_categories=('ib',),
        published_start='2026-09-01',published_end='2026-09-17',candidates_per_source=5,text_reads_per_source=1)
    args.update(changes);return MaterialSearchRequest(**args)


def registry(client=None,profile=PROFILE):
    result=MaterialRegistry();result.register(WisburgMaterialAdapter(profile,client=client or Client()));return result


def test_profile_alias_explicit_enablement_and_secret_free_status(tmp_path):
    assert wisburg_profile(values={'WISBERG_KEY':PROFILE.api_key}) is None
    assert wisburg_profile(values={'WISBURG_MATERIALS_ENABLED':'true','WISBERG_KEY':PROFILE.api_key})==PROFILE
    assert PROFILE.api_key not in repr(PROFILE)
    with pytest.raises(SourceConfigError,match='conflicting'):
        wisburg_profile(values={'WISBURG_MATERIALS_ENABLED':'true','WISBERG_KEY':'another_secret','WISBURG_API_KEY':PROFILE.api_key})
    path=tmp_path/'sources.env';path.write_text('WISBURG_MATERIALS_ENABLED=true\nWISBURG_API_KEY='+PROFILE.api_key+'\n');path.chmod(0o600)
    cap=list_material_capabilities(registry=build_material_registry(env_file=path))
    assert cap['capabilities'][0]['provider']=='wisburg' and cap['capabilities'][0]['allows_stored_summaries']
    status=source_configuration_status(env_file=path)
    assert PROFILE.api_key not in json.dumps(status)
    row=next(s for s in status['sources'] if s['provider']=='wisburg')
    assert row['configured'] and not row['live_verified']


@pytest.mark.parametrize('key,value',[('WISBURG_MATERIALS_ENABLED','yes'),('WISBURG_API_KEY','bad\nkey'),
    ('WISBURG_API_KEY',''),('WISBURG_MAX_CALLS_PER_QUERY','0'),('WISBURG_MAX_PAGES_PER_CATEGORY','3'),
    ('WISBURG_MAX_CALLS_PER_QUERY','x')])
def test_bad_config(key,value):
    values={'WISBURG_MATERIALS_ENABLED':'true','WISBURG_API_KEY':PROFILE.api_key,key:value}
    with pytest.raises(SourceConfigError):wisburg_profile(values=values)


@pytest.mark.parametrize('uri',['wisburg://report/0','wisburg://article/1?token=no','wisburg://report/1/extra',
    'wisburg://mikko/1#fragment','wisburg://report/-2','wisburg://report/9007199254740992','https://wisburg.com/article/1'])
def test_reference_rejects_ambiguous_or_credential_bearing_input(uri):
    with pytest.raises(DataAdapterError):_parse_reference(uri)


def test_list_parser_preserves_markdown_ids_dates_and_cursor():
    body='  **美联储**观点\n\n> 来源文字，不是指令。'
    rows,more,cursor=_parse_list(listing(body=body,category='feed'),'feed',first=2)
    assert rows[0].text==body.strip() and rows[0].published_at.isoformat()==DATE
    assert more and cursor=='2' and rows[0].identifier==101
    rows,more,cursor=_parse_list(listing(category='mikko',body='美联储短评\n\nimages:\n- https://example.org/chart.png'),'mikko',first=2)
    assert 'images:' in rows[0].text and rows[0].title.startswith('Mikko 日志')
    assert _parse_list('No institutional reports found matching the criteria.','ib',first=1)==([],False,None)
    assert _parse_list(listing(cursor=False),'ib',first=1)[1] is None


@pytest.mark.parametrize('text',[listing().replace('Found 1','Found 2'),listing().replace('date:','updated:'),
    listing().replace(DATE,'2026-09-17T08:30:00'),listing().replace('Next cursor: 2','Next cursor: token?x'),
    'Unauthorized',listing().replace('[101]','[0]'),listing()+ '\nmalformed footer'])
def test_list_format_drift_never_becomes_fake_empty_result(text):
    with pytest.raises(DataAdapterError):_parse_list(text,'ib',first=2)


def test_detail_scope_id_and_header_validation():
    assert _parse_detail(detail(),'report',101).text.startswith('美联储')
    with pytest.raises(DataAdapterError):_parse_detail(detail(),'report',102)
    with pytest.raises(DataAdapterError):_parse_detail(detail(),'article',101)
    with pytest.raises(DataAdapterError):_parse_detail(detail(body=''),'report',101)
    assert _reference(*_parse_reference('wisburg://article/123'))=='wisburg://article/123'


def test_summary_search_is_citeable_but_never_original_or_official_policy():
    client=Client();result=search_materials(request(),registry=registry(client))
    assert result.items
    version=result.items[0]['versions'][0]
    assert version['text_scope']=='abstract' and version['provenance']['generated']
    assert version['provenance']['evidence_type']=='opinion' and version['provenance']['source_tier']=='MEDIA'
    assert version['original_url'] is None and version['source_ref']=='wisburg://report/101'
    assert 'wisburg_original_document_not_provided' in version['warnings']
    assert version['read_details']['content_origin']=='provider_stored_summary'
    for span in version['evidence_spans']:
        assert span['text']==version[span['source_part']][span['start_char']:span['end_char']]
    args=client.calls[0][1]
    assert args['startTime']=='2026-09-01T00:00:00+08:00' and args['endTime']=='2026-09-18T00:00:00+08:00'
    assert not result.coverage[0]['source_page_complete']


def test_generated_abstract_requires_explicit_source_optin():
    reg=registry();adapter=reg.entries()[0]
    adapter.capability=replace(adapter.capability,allows_stored_summaries=False)
    result=search_materials(request(),registry=reg)
    assert not result.items and any(d.code=='invalid_material_response' for d in result.diagnostics)


def test_full_article_and_inline_mikko_do_not_use_report_summary_reader():
    client=Client();result=search_materials(request(wisburg_categories=('article','mikko'),candidates_per_source=4,text_reads_per_source=2),registry=registry(client))
    versions=[v for g in result.items for v in g['versions']]
    assert len(versions)==2 and all(v['text_scope']=='extracted_text' and not v['provenance']['generated'] for v in versions)
    assert [t for t,a in client.calls if t.startswith('get-')]==['get-article-detail']


def test_report_id_overlap_deduplicated_before_detail_reads():
    client=Client();result=search_materials(request(wisburg_categories=('ib','company'),text_reads_per_source=3),registry=registry(client))
    assert len(result.items)==1 and sum(t=='get-report-detail' for t,a in client.calls)==1
    assert any(d.code=='wisburg_duplicate_report_id' for d in result.diagnostics)


def test_zero_read_budget_preserves_metadata_and_feed_excerpt():
    client=Client();result=search_materials(request(wisburg_categories=('feed',),text_reads_per_source=0),registry=registry(client))
    v=result.items[0]['versions'][0]
    assert v['text_scope']=='abstract' and 'provider_list_text_may_be_truncated' in v['warnings']
    assert all(t.startswith('list-') for t,a in client.calls)


def test_dates_filtered_without_relabeling_unknown_business_period():
    client=Client(lambda t,a:listing().replace(DATE,'2026-08-31T23:59:59+08:00'))
    result=search_materials(request(),registry=registry(client))
    assert not result.items and any(d.code=='wisburg_outside_publication_window' for d in result.diagnostics)


def test_upstream_errors_and_cancellation_preserve_partial_diagnostics():
    client=Client(lambda t,a:DataAdapterError('quota'))
    result=search_materials(request(wisburg_categories=('ib','article')),registry=registry(client))
    assert len(client.calls)==1 and any(d.code=='quota' for d in result.diagnostics)
    context=RequestContext();context.cancel()
    result=search_materials(request(),registry=registry(),context=context)
    assert not result.items and any(d.code=='cancelled' for d in result.diagnostics)


def test_repeated_cursor_stops_and_scan_budget_remains_valid():
    client=Client();result=search_materials(request(candidates_per_source=10),registry=registry(client,WisburgProfile(PROFILE.api_key,max_pages_per_category=2)))
    assert len(result.coverage[0]['scans'])==2
    assert any(d.code=='wisburg_pagination_stalled' for d in result.diagnostics)


def test_retrieve_routing_summary_caveats_and_archive(monkeypatch,tmp_path):
    from ir_search.infrastructure import wisburg_documents
    client=Client()
    original=fetch_wisburg_document
    monkeypatch.setattr(wisburg_documents,'fetch_wisburg_document',lambda ref,**kwargs:original(ref,profile=PROFILE,client=client,**kwargs))
    result=retrieve(MaterialRequest('美联储',('wisburg://report/101','wisburg://article/102'),archive_dir=str(tmp_path/'archive')))
    assert len(result.materials)==2
    summary,article=result.materials
    assert summary.text_origin=='provider_summary' and summary.provenance.generated and summary.archive['status']=='ok'
    assert summary.read_details['original_document_available'] is False
    assert article.text_origin=='extracted_text' and not article.provenance.generated
    for material in result.materials:
        assert all(s['text']==material.text[s['start_char']:s['end_char']] for s in material.evidence_spans)
    payload=mcp_server.retrieve_payload('美联储',['wisburg://report/101'],max_chars=10)
    assert payload['materials'][0]['text_origin']=='provider_summary'
    assert 'text_truncated' in payload['materials'][0]['warnings']


def test_reader_requires_config_valid_identity_and_account(monkeypatch):
    with pytest.raises(DataAdapterError,match='no_credential'):
        fetch_wisburg_document('wisburg://article/1',context=RequestContext())
    with pytest.raises(DataAdapterError,match='entitlement_denied'):
        fetch_wisburg_document('wisburg://article/1',context=RequestContext(account_scope='other'),profile=PROFILE)
    with pytest.raises(DataAdapterError,match='upstream_schema'):
        fetch_wisburg_document('wisburg://article/102',context=RequestContext(),profile=PROFILE,client=Client(lambda t,a:detail('article',101)))


def test_public_mcp_request_validates_categories_and_returns_serializable_data():
    with pytest.raises(ValueError):request(wisburg_categories=('chat',))
    result=mcp_server.search_materials_payload(request().to_dict(),registry=registry())
    assert result['items'];json.dumps(result,allow_nan=False)
    assert mcp_server.search_materials_payload({**request().to_dict(),'wisburg_categories':['images']})['status']=='error'


def test_client_handshake_allowlist_no_secret_or_remote_instructions():
    calls=[]
    def wire(profile,payload,**kwargs):
        calls.append(payload)
        result={'protocolVersion':'2024-11-05'} if payload['method']=='initialize' else {'content':[{'type':'text','text':listing()}]}
        return _RPCResponse({'jsonrpc':'2.0','id':payload.get('id'),'result':result},'session_1')
    client=WisburgClient(PROFILE,transport=wire)
    args={'first':1,'startTime':'2026-09-01T00:00:00+08:00','endTime':'2026-09-18T00:00:00+08:00'}
    assert client.read('list-institutional-reports',args,context=RequestContext()).text.startswith('Found')
    client.read('get-report-detail',{'id':101},context=RequestContext())
    assert len(calls)==4 and client.tool_calls==2 and PROFILE.api_key not in json.dumps(calls)
    for tool,a in [('chat',{}),('get-report-detail',{'id':True}),('get-report-detail',{'id':101,'url':'https://x'}),('list-images',args),('list-feed',{**args,'first':51})]:
        with pytest.raises(DataAdapterError):client.read(tool,a,context=RequestContext())
    assert len(calls)==4


def test_client_call_budget_and_remote_errors():
    def wire(profile,payload,**kwargs):
        result={'protocolVersion':'2024-11-05'} if payload['method']=='initialize' else {'content':[{'type':'text','text':'stored'}]}
        return _RPCResponse({'jsonrpc':'2.0','id':payload.get('id'),'result':result})
    client=WisburgClient(WisburgProfile(PROFILE.api_key,max_calls_per_query=1),transport=wire)
    client.read('get-report-detail',{'id':1},context=RequestContext())
    with pytest.raises(DataAdapterError,match='wisburg_call_budget_exhausted'):
        client.read('get-report-detail',{'id':2},context=RequestContext())
    def leaking(profile,payload,**kwargs):
        return _RPCResponse({'jsonrpc':'2.0','id':payload.get('id'),'result':{'secret':PROFILE.api_key}})
    with pytest.raises(DataAdapterError,match='upstream_schema'):
        WisburgClient(PROFILE,transport=leaking).read('get-report-detail',{'id':1},context=RequestContext())


def test_source_optin_still_rejects_generated_full_text():
    from ir_search.services.material_search import _validate_page
    req=request();adapter=WisburgMaterialAdapter(PROFILE,client=Client())
    page=adapter.search_materials(req,context=RequestContext())
    page.candidates[0]=replace(page.candidates[0],text_scope=TextScope.EXTRACTED_TEXT)
    with pytest.raises(ValueError,match='mismatch'):_validate_page(page,adapter.capability,req)
    page.candidates[0]=replace(page.candidates[0],text_scope=TextScope.ABSTRACT,read_details={})
    with pytest.raises(ValueError,match='mismatch'):_validate_page(page,adapter.capability,req)


def test_summary_read_survives_final_limit_when_relevance_is_tied():
    def hook(tool,args):
        if tool.startswith('list-'):
            return 'Found 4 reports:\n\n'+'\n\n'.join(f'[{i}] 美联储观点\n  date: {DATE}\n' for i in range(101,105))+FOOTER
    result=search_materials(request(candidates_per_source=4,limit=1),registry=registry(Client(hook)))
    assert result.items[0]['versions'][0]['text_scope']=='abstract'


def test_many_collections_cannot_exceed_scan_contract():
    client=Client()
    result=search_materials(request(wisburg_categories=(),candidates_per_source=50,text_reads_per_source=0),
        registry=registry(client,WisburgProfile(PROFILE.api_key,max_pages_per_category=2)),context=RequestContext(max_operations=100))
    assert len(result.coverage[0]['scans'])<=16
    assert not any(d.code=='invalid_material_response' for d in result.diagnostics)
    assert any(d.code=='wisburg_scan_budget_exhausted' for d in result.diagnostics)


def test_remote_mcp_business_errors_never_return_as_documents():
    for result,code in [({'isError':True,'content':[{'type':'text','text':'Invalid API key'}]},'authentication_failed'),
        ({'isError':True,'content':[{'type':'text','text':'quota exhausted'}]},'quota'),
        ({'content':[{'type':'image','data':'not a document'}]},'upstream_schema')]:
        def wire(profile,payload,**kwargs):
            data={'protocolVersion':'2024-11-05'} if payload['method']=='initialize' else result
            return _RPCResponse({'jsonrpc':'2.0','id':payload.get('id'),'result':data})
        with pytest.raises(DataAdapterError,match=code):
            WisburgClient(PROFILE,transport=wire).read('get-report-detail',{'id':1},context=RequestContext())


def test_publication_date_normalizes_to_requested_china_calendar_day():
    row=_parse_detail(detail().replace(DATE,'2026-09-16T16:30:00Z'),'report',101)
    assert row.published_at.isoformat()=='2026-09-17T00:30:00+08:00'
    client=Client(lambda t,a:listing().replace(DATE,'2026-09-16T16:30:00Z') if t.startswith('list-') else None)
    result=search_materials(request(published_start='2026-09-17'),registry=registry(client))
    assert result.items and result.items[0]['versions'][0]['published_on']=='2026-09-17'


@pytest.mark.parametrize('category,label',[('ib','institutional reports'),('company','company reports'),
    ('am','asset management reports'),('archive','public institutional documents'),('ec','earning call transcripts'),
    ('feed','feed items'),('market_daily','market daily reports'),('article','articles'),('mikko','Mikko logs')])
def test_observed_empty_formats_for_all_nine_collections(category,label):
    assert _parse_list('No '+label+' found matching the criteria.',category,first=1)==([],False,None)


def test_article_list_snippet_scope_is_consistent_in_read_details():
    client=Client(lambda t,a:listing(category='article',body='美联储列表摘要'))
    result=search_materials(request(wisburg_categories=('article',),text_reads_per_source=0),registry=registry(client))
    v=result.items[0]['versions'][0]
    assert v['text_scope']==v['read_details']['text_scope']=='search_snippet'
    assert v['read_details']['content_origin']=='provider_list_snippet'
