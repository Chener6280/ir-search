"""Offline IMA contracts: protocol drift, permission boundaries, budgets and exact citations."""
from dataclasses import replace
from datetime import datetime, timezone
from io import BytesIO
import json
import zipfile
import pytest

from ir_search import (MaterialSearchRequest, MaterialRegistry, RequestContext, search_materials,
    MaterialRequest, retrieve, build_material_registry)
from ir_search.adapters.ima_materials import IMAMaterialAdapter
from ir_search.infrastructure.credentials import IMAProfile, ima_profile, SourceConfigError, source_configuration_status
from ir_search.infrastructure.ima import IMAClient, IMAReply, _ref, _parse_ref, _page
from ir_search.infrastructure.ima_documents import fetch_ima_document, _office_text, _download
from ir_search.infrastructure.public_web import _Reply, _request
from ir_search.registry import DataAdapterError
from ir_search import mcp_server

NOW=datetime(2026,9,16,tzinfo=timezone.utc)
PROFILE=IMAProfile('synthetic_ima_api_key','synthetic_ima_client_id',('kb1',))
TEXT='茅台动销记录：渠道观察存在样本偏差，需要与其他材料交叉核对。'


def req(**changes):
    return MaterialSearchRequest(**dict(dict(question='茅台动销',keywords=['动销'],providers=['ima'],
        published_start='2026-09-01',published_end='2026-09-16',candidates_per_source=10,text_reads_per_source=2),**changes))


class Client:
    def __init__(self,changes=None):self.calls=[];self.changes=changes or {}
    def read(self,operation,p,*,context):
        context.begin_operation();self.calls.append((operation,p))
        data={
            'search_knowledge_base':{'info_list':[{'kb_id':'kb1','kb_name':'研究库'}],'is_end':True},
            'search_knowledge':{'info_list':[{'media_id':'m1','title':'茅台动销文档','highlight_content':'<em>动销</em>摘要','media_type':11}]},
            'search_note':{'search_note_infos':[{'note_book_info':{'note_id':'n1','title':'茅台动销笔记','summary':'茅台动销观察',
                'create_time':'1789430400000','modify_time':1789516800000}}],'is_end':True},
            'get_media_info':{'media_type':11,'notebook_ext_info':{'notebook_id':'n1'}},
            'get_doc_content':{'content':TEXT},
        }[operation]
        selected=self.changes.get(operation,data)
        if isinstance(selected,Exception):raise selected
        if callable(selected):selected=selected(p)
        return IMAReply(selected,NOW)


def setup(client=None,profile=PROFILE):
    client=client or Client();registry=MaterialRegistry();registry.register(IMAMaterialAdapter(profile,client=client))
    return registry,client


def test_search_originals_notes_dates_and_version_bound_spans():
    reg,client=setup()
    result=search_materials(req(),registry=reg).to_dict()
    assert result['status']=='partial' and not result['complete'] and result['items']
    versions=[v for item in result['items'] for v in item['versions']]
    assert len(versions)==2
    assert all(v['text_scope']=='extracted_text' and v['text_provider']=='ima' for v in versions)
    note=next(v for v in versions if v['source_record_type']=='ima_personal_note')
    assert note['published_at'] is None and note['published_on'] is None and note['source_created_at']
    assert note['provenance']['authority']=='unknown' and note['provenance']['evidence_type']=='unknown'
    assert all(v['original_url'] is None for v in versions)
    for v in versions:
        for span in v['evidence_spans']:
            assert span['text']==v[span['source_part']][span['start_char']:span['end_char']]
    assert 'ima_pagination_unknown' in {d['code'] for d in result['diagnostics']}
    assert result['coverage'][0]['scans'][0]['search_query']=='动销'
    assert ('get_doc_content',{'note_id':'n1','target_content_format':0}) in client.calls


def test_permission_failure_retains_snippet_not_fulltext():
    reg,_=setup(Client({'get_media_info':DataAdapterError('entitlement_denied')}))
    result=search_materials(req(ima_include_notes=False),registry=reg).to_dict()
    v=result['items'][0]['versions'][0]
    assert v['text_scope']=='search_snippet' and 'original_text_fetch_failed' in v['warnings']
    assert any(d['code']=='entitlement_denied' for d in result['diagnostics'])


def test_registry_and_configuration_do_not_reveal_private_values(tmp_path):
    env=tmp_path/'config.env';env.write_text('IMA_MATERIALS_ENABLED=true\nIMA_API_KEY='+PROFILE.api_key+'\nIMA_CLIENT_ID='+PROFILE.client_id+'\nIMA_KNOWLEDGE_BASE_IDS=kb1\n');env.chmod(0o600)
    assert ima_profile(values={}) is None
    assert ima_profile(env_file=env)==PROFILE
    assert [a.name for a in build_material_registry(env_file=env).entries()]==['ima']
    status=source_configuration_status(env_file=env)
    item=next(s for s in status['sources'] if s['provider']=='ima')
    assert item['configured'] and not item['live_verified'] and item['configured_knowledge_base_count']==1
    for secret in (PROFILE.api_key,PROFILE.client_id,'kb1'):
        assert secret not in json.dumps(status) and secret not in repr(PROFILE)
    reg,_=setup()
    assert mcp_server.search_materials_payload(req().to_dict(),registry=reg)['items']


@pytest.mark.parametrize('changes',[{'IMA_MATERIALS_ENABLED':'yes'},{'IMA_MATERIALS_ENABLED':'true'},
    {'IMA_INCLUDE_NOTES':'yes'},{'IMA_MAX_PAGES':'3'},{'IMA_MAX_KNOWLEDGE_BASES':'4'},
    {'IMA_CLIENT_ID':'x\ny'},{'IMA_KNOWLEDGE_BASE_IDS':'kb1,kb1'},{'IMA_API_KEY':'invalid header'}])
def test_bad_configuration(changes):
    values={'IMA_MATERIALS_ENABLED':'true','IMA_API_KEY':PROFILE.api_key,'IMA_CLIENT_ID':PROFILE.client_id}
    if changes=={'IMA_MATERIALS_ENABLED':'true'}:values={}
    with pytest.raises(SourceConfigError):ima_profile(values={**values,**changes})


@pytest.mark.parametrize('changes',[{'ima_include_notes':1},{'ima_include_notes':'true'},
    {'ima_knowledge_base_ids':'kb1'},{'ima_knowledge_base_ids':['x'*513]},{'ima_knowledge_base_ids':['a/b']}])
def test_bad_request(changes):
    with pytest.raises(ValueError):req(**changes)


def test_scope_limits_query_selection_missing_dates_and_read_budget():
    reg,c=setup()
    r=search_materials(req(published_start=None,published_end=None),registry=reg)
    assert r.required_inputs and not c.calls
    r=search_materials(req(ima_knowledge_base_ids=['unconfigured']),registry=reg)
    assert not c.calls and any(d.code=='entitlement_denied' for d in r.diagnostics)
    r=search_materials(req(text_reads_per_source=0,ima_include_notes=False),registry=reg).to_dict()
    assert r['items'][0]['versions'][0]['text_scope']=='search_snippet'
    assert all(op!='get_media_info' for op,p in c.calls)
    r=search_materials(req(),registry=reg,context=RequestContext(max_operations=1)).to_dict()
    assert any(d['code']=='operation_budget_exhausted' for d in r['diagnostics'])


def test_bounded_directory_aliases_and_pagination_stall():
    p=replace(PROFILE,knowledge_base_ids=(),max_knowledge_bases=1)
    c=Client({'search_knowledge_base':{'info_list':[{'id':'kb1','name':'库1'},{'kb_id':'kb2','kb_name':'库2'}],'is_end':False,'next_cursor':'next'},
        'search_knowledge':{'info_list':[{'media_id':'m1','title':'茅台动销','highlight_content':'动销'}],'is_end':False,'next_cursor':'repeat'}})
    reg,_=setup(c,p)
    r=search_materials(req(ima_include_notes=False,text_reads_per_source=0),registry=reg).to_dict()
    codes={d['code'] for d in r['diagnostics']}
    assert {'ima_directory_not_exhaustive','ima_knowledge_base_budget_exhausted','ima_pagination_stalled'}<=codes
    assert len(r['items'])==1 and all(p.get('knowledge_base_id','kb1')=='kb1' for op,p in c.calls)


def test_default_context_account_isolation_and_safe_refs():
    for kind in ('media','note'):
        assert _parse_ref(_ref(kind,'id+=123'))==(kind,'id+=123')
    for ref in ('ima://note/../x','ima://note/a?key=secret','ima://evil/a','ima://note/%2Fabc','ima://note/a#fragment'):
        with pytest.raises(DataAdapterError):_parse_ref(ref)
    with pytest.raises(DataAdapterError,match='entitlement_denied'):
        fetch_ima_document('ima://note/a',profile=PROFILE,client=Client(),context=RequestContext(account_scope='other'))
    with pytest.raises(DataAdapterError,match='no_credential'):
        fetch_ima_document('ima://note/a',context=RequestContext())


def transport_result(data,code=0,status=200):
    def transport(url,**kwargs):
        assert url.startswith('https://ima.qq.com/openapi/')
        assert kwargs['headers']['ima-openapi-apikey']==PROFILE.api_key
        assert kwargs['headers']['ima-openapi-clientid']==PROFILE.client_id
        kwargs['context'].begin_operation()
        return _Reply(status,'application/json','https://evil.example/',json.dumps({'code':code,'msg':'do not publish '+PROFILE.api_key,'data':data}).encode(),NOW)
    return transport


def test_client_fixed_host_literal_payload_and_secret_echo_failure():
    observed=[]
    def wire(url,**kw):
        observed.append((url,kw));kw['context'].begin_operation()
        return _Reply(200,'application/json','',b'{"code":0,"data":{"content":"okay"}}',NOW)
    client=IMAClient(PROFILE,transport=wire)
    assert client.read('get_doc_content',{'note_id':'n1','target_content_format':0},context=RequestContext()).data=={'content':'okay'}
    assert observed[0][0]=='https://ima.qq.com/openapi/note/v1/get_doc_content'
    assert json.loads(observed[0][1]['body'])=={'note_id':'n1','target_content_format':0}
    with pytest.raises(DataAdapterError,match='unsupported'):
        client.read('append_doc',{},context=RequestContext())
    with pytest.raises(DataAdapterError,match='unsupported'):
        client.read('get_doc_content',{'doc_id':'n1','target_content_format':0},context=RequestContext())
    with pytest.raises(DataAdapterError,match='upstream_schema'):
        IMAClient(PROFILE,transport=transport_result({})).read('get_media_info',{'media_id':'m1'},context=RequestContext())


@pytest.mark.parametrize('obj,expected',[
    ({'code':220030,'msg':'no permission','data':{}},'entitlement_denied'),
    ({'code':210011,'data':{}},'entitlement_denied'),
    ({'code':110021,'data':{}},'rate_limit'),({'code':999999,'data':{}},'ima_upstream_rejected'),
    ({'retcode':0,'data':{}},'upstream_schema'),({'code':False,'data':{}},'upstream_schema'),
    ({'code':0,'data':[]},'upstream_schema'),({'code':0,'data':{'token':PROFILE.api_key}},'upstream_schema'),
])
def test_client_errors_do_not_expose_vendor_messages(obj,expected):
    wire=lambda *a,**kw:_Reply(200,'application/json','',json.dumps(obj).encode(),NOW)
    with pytest.raises(DataAdapterError,match=expected) as e:
        IMAClient(PROFILE,transport=wire).read('get_media_info',{'media_id':'m1'},context=RequestContext())
    assert PROFILE.api_key not in str(e.value)


def test_client_redirects_and_unexpected_errors_are_closed():
    with pytest.raises(DataAdapterError,match='blocked_url'):
        IMAClient(PROFILE,transport=transport_result({},status=302)).read('get_media_info',{'media_id':'m1'},context=RequestContext())
    def fail(*a,**kw):raise RuntimeError(PROFILE.api_key)
    with pytest.raises(DataAdapterError,match='network'):
        IMAClient(PROFILE,transport=fail).read('get_media_info',{'media_id':'m1'},context=RequestContext())


def test_retrieve_note_mapping_and_character_citations(monkeypatch):
    c=Client()
    monkeypatch.setattr('ir_search.infrastructure.ima_documents.ima_profile',lambda:PROFILE)
    monkeypatch.setattr('ir_search.infrastructure.ima_documents.IMAClient',lambda p:c)
    r=retrieve(MaterialRequest(question='茅台动销',urls=['ima://media/m1','ima://note/n1'])).to_dict()
    assert len(r['materials'])==2
    for m in r['materials']:
        assert m['provenance']['provider']=='ima' and m['provenance']['authority']=='unknown'
        assert m['original_url'] is None and m['text_provider']=='ima'
        for span in m['evidence_spans']:assert span['text']==m['text'][span['start_char']:span['end_char']]


@pytest.mark.parametrize('data,expected',[
    ({'media_type':1},'ima_original_unavailable'),({'media_type':12},'web_content_unsupported'),
    ({'media_type':11,'notebook_ext_info':{}},'upstream_schema'),
    ({'media_type':1,'url_info':{'url':'https://evil.example/a?signature=secret'}},'blocked_url'),
    ({'media_type':2,'url_info':{'url':'https://example.org','headers':{'Authorization':'token'}}},'blocked_url'),
])
def test_unsupported_originals_are_not_empty_success(data,expected):
    with pytest.raises(DataAdapterError,match=expected):
        fetch_ima_document('ima://media/m1',profile=PROFILE,client=Client({'get_media_info':data}),context=RequestContext())


def test_signed_download_never_exposes_url_headers_or_html_canonical():
    url='https://res-pkb.ima.qq.com/a?signature=secret';headers={'X-IMA-Sign':'private_sign'}
    c=Client({'get_media_info':{'media_type':20,'url_info':{'url':url,'headers':headers}}})
    def download(u,h,**kwargs):
        assert u==url and h==headers
        return _Reply(200,'text/html','',b'<link rel="canonical" href="https://evil.example/?key=secret"><title>test</title><p>Research demand</p>',NOW)
    d=fetch_ima_document('ima://media/m1',profile=PROFILE,client=c,context=RequestContext(),downloader=download)
    encoded=json.dumps(d.to_dict())
    assert d.url=='ima://media/m1' and d.canonical_url is None and d.links==[]
    assert all(v not in encoded for v in ('private_sign','signature=secret','evil.example','res-pkb.ima.qq.com'))


@pytest.mark.parametrize('url,headers',[
    ('https://evil.example/a',{'Authorization':'token'}),
    ('https://res-pkb.ima.qq.com/a',{'ima-openapi-apikey':'key'}),
    ('https://res-pkb.ima.qq.com/a',{'Host':'evil.example'}),
    ('https://res-pkb.ima.qq.com/a',{'X-IMA-Sign':'x\r\ny'}),
    ('http://res-pkb.ima.qq.com/a',{'X-IMA-Sign':'token'}),
])
def test_scoped_headers_rejected_before_network(url,headers,monkeypatch):
    monkeypatch.setattr('ir_search.infrastructure.public_web._resolve',lambda *a:pytest.fail('network reached'))
    with pytest.raises(DataAdapterError,match='blocked_url'):
        _request(url,context=RequestContext(),signed_download=True,scoped_download_headers=headers)


def test_download_redirect_no_auth_forwarding(monkeypatch):
    calls=[]
    def wire(*a,**kw):calls.append(kw);return _Reply(302,'','https://evil.example/',b'',NOW)
    monkeypatch.setattr('ir_search.infrastructure.ima_documents._request',wire)
    with pytest.raises(DataAdapterError,match='blocked_url'):
        _download('https://res-pkb.ima.qq.com/a',{'X-IMA-Sign':'sign'},context=RequestContext())
    assert len(calls)==1 and calls[0]['scoped_download_headers']=={'X-IMA-Sign':'sign'}


def office_zip(name,xml):
    output=BytesIO()
    with zipfile.ZipFile(output,'w') as archive:archive.writestr(name,xml)
    return output.getvalue()


@pytest.mark.parametrize('kind,name,namespace',[
    (3,'word/document.xml','http://schemas.openxmlformats.org/wordprocessingml/2006/main'),
    (4,'ppt/slides/slide1.xml','http://schemas.openxmlformats.org/drawingml/2006/main'),
])
def test_docx_pptx_bounded_text_only(kind,name,namespace):
    raw=office_zip(name,'<root xmlns:a="'+namespace+'"><a:p><a:r><a:t>'+TEXT+'</a:t></a:r></a:p></root>')
    text,truncated=_office_text(raw,kind,context=RequestContext(),max_chars=8)
    assert text==TEXT[:8] and truncated
    c=Client({'get_media_info':{'media_type':kind,'url_info':{'url':'https://res-pkb.ima.qq.com/file','headers':{}}}})
    doc=fetch_ima_document('ima://media/m1',profile=PROFILE,client=c,context=RequestContext(),
        downloader=lambda *a,**kw:_Reply(200,'application/zip','',raw,NOW))
    assert doc.text==TEXT and 'office_text_only_images_charts_notes_not_extracted' in doc.warnings


def test_office_entities_and_archive_bomb_are_rejected():
    raw=office_zip('word/document.xml','<!DOCTYPE x [<!ENTITY e "boom">]><x/>')
    with pytest.raises(DataAdapterError,match='web_content_unsupported'):_office_text(raw,3,context=RequestContext(),max_chars=100)
    raw=office_zip('word/document.xml','<!DOCTYPE x [<!ENTITY e "boom">]><x>&e;</x>'.encode('utf-16'))
    with pytest.raises(DataAdapterError,match='web_content_unsupported'):_office_text(raw,3,context=RequestContext(),max_chars=100)
    with pytest.raises(DataAdapterError,match='web_content_unsupported'):_office_text(b'legacy binary ppt',4,context=RequestContext(),max_chars=100)


def test_pagination_shape_and_unknown_flags():
    assert _page({'info_list':[]},'info_list')==([],None,None)
    for data in ({'info_list':None},{'info_list':[None]},{'info_list':[],'is_end':0},{'info_list':[],'next_cursor':'x'*513}):
        with pytest.raises(DataAdapterError):_page(data,'info_list')


def test_maximum_scans_stay_inside_service_contract_and_notes_paginate():
    p=replace(PROFILE,knowledge_base_ids=('a','b','c'))
    def search(p):
        return {'info_list':[{'media_id':p['knowledge_base_id']+p['cursor']+str(len(p['query'])),
                'title':'茅台动销库存','highlight_content':'动销'}],'is_end':False,'next_cursor':p['cursor']+'n'}
    def notes(p):
        return {'search_note_infos':[{'note_book_info':{'note_id':'note'+str(p['start']),
            'title':'茅台动销库存','summary':'动销'}}],'is_end':False}
    c=Client({'search_knowledge':search,'search_note':notes});reg,_=setup(c,p)
    result=search_materials(req(keywords=['动销','库存','第三个'],candidates_per_source=50,text_reads_per_source=0),registry=reg).to_dict()
    scans=result['coverage'][0]['scans']
    assert len(scans)==16 and sum(s['inspected_count'] for s in scans)==16
    assert 'ima_query_budget_exhausted' in {d['code'] for d in result['diagnostics']}
    assert any(op=='search_note' and p['start']==1 for op,p in c.calls)


def test_note_plaintext_truncation_and_empty_content():
    d=fetch_ima_document('ima://note/n1',profile=PROFILE,client=Client(),context=RequestContext(),max_chars=5)
    assert d.text==TEXT[:5] and 'text_truncated' in d.warnings
    for body in ('',None):
        with pytest.raises(DataAdapterError):
            fetch_ima_document('ima://note/n1',profile=PROFILE,client=Client({'get_doc_content':{'content':body}}),context=RequestContext())


def test_pdf_source_is_conservative_and_unknown_publication():
    fitz=pytest.importorskip('fitz')
    pdf=fitz.open();page=pdf.new_page();page.insert_text((72,72),'Demand evidence from an unverified publisher.');raw=pdf.tobytes();pdf.close()
    c=Client({'get_media_info':{'media_type':1,'url_info':{'url':'https://res-pkb.ima.qq.com/a','headers':{}}}})
    doc=fetch_ima_document('ima://media/m1',profile=PROFILE,client=c,context=RequestContext(),
        downloader=lambda *a,**kw:_Reply(200,'application/pdf','',raw,NOW))
    assert 'Demand evidence' in doc.text and doc.evidence_type.value=='unknown' and doc.published_at is None
    assert doc.title=='IMA 文档' and doc.source=='ima'


def test_ima_public_url_preserves_fetch_identity_without_canonical_injection(monkeypatch):
    from ir_search.documents.html import extract_html_document
    url='https://example.org/article'
    original=extract_html_document(b'<title>Demand</title><meta name="pubdate" content="2025-01-01"><link rel="canonical" href="https://bad.example/?key=secret"><p>Demand evidence</p>',url)
    c=Client({'get_media_info':{'media_type':2,'url_info':{'url':url}}})
    monkeypatch.setattr('ir_search.infrastructure.ima_documents.fetch_public_document',lambda *a,**kw:original)
    doc=fetch_ima_document('ima://media/m1',profile=PROFILE,client=c,context=RequestContext())
    assert doc.canonical_url==url and doc.published_at.year==2025 and doc.links==[]
    # Search applies the original publication date after reading; insertion time is irrelevant.
    reg,_=setup(c)
    result=search_materials(req(ima_include_notes=False),registry=reg).to_dict()
    assert not result['items'] and 'ima_outside_publication_window' in {d['code'] for d in result['diagnostics']}
