"""Synthetic account-material contracts; no credentials, browsers or network required."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path

import pytest

from ir_search import MaterialRequest, MaterialSearchRequest, RequestContext, retrieve, search_materials
from ir_search import mcp_server
from ir_search.context import RequestStopped
from ir_search.infrastructure.credentials import SourceConfigError, source_configuration_status
from ir_search.infrastructure.alphapai import (AlphapaiProfile, alphapai_profile, AlphapaiClient,
    AlphapaiResponse, _reference, _parse_reference, _validate, _payload, _checked_reply, _error_code)
from ir_search.infrastructure.alphapai_cache import _DetailCache
from ir_search.infrastructure.alphapai_documents import fetch_alphapai_document, _document, _transcript
from ir_search.adapters.alphapai_materials import AlphapaiMaterialAdapter
from ir_search.material_registry import MaterialRegistry, build_material_registry, list_material_capabilities
from ir_search.registry import DataAdapterError

NOW = datetime.now(timezone.utc)
ID = 'synthetic_meeting_identifier_00001'
PROFILE = AlphapaiProfile('10000000000', 'synthetic_alpha_password', cache_ttl_seconds=0)


def row(identifier=ID, **changes):
    value = {'id':identifier, 'title':'茅台渠道经销商交流', 'publishInstitution':'某证券',
        'date':'2026-09-11 15:54:08', 'roadshowDate':'2026-09-11 15:00:00',
        'content':'茅台动销线索摘要', 'stock':[{'code':'600519.SH','name':'贵州茅台'}],
        'hasPermission':False, 'freeAccess':True, 'sharePermission':{'hasPermission':True},
        'aiSummary':{'content':'<p>茅台动销的供应商 AI 会议摘要，未经独立核实。</p>', 'wordCount':1000},
        'mtSummary':{'content':json.dumps([
            {'bg':20400,'ed':21000,'role':'2','content':'茅台动销情况。'},
            {'bg':22000,'ed':23000,'role':'3','content':'这是部分转录。'}], ensure_ascii=False), 'wordCount':10000},
        'recLength':2898}
    value.update(changes); return value


def request(**changes):
    value = dict(question='茅台动销', entities=('茅台',), keywords=('动销',), providers=('alphapai',),
        published_start='2026-09-01', published_end='2026-09-18', candidates_per_source=5, text_reads_per_source=1)
    value.update(changes); return MaterialSearchRequest(**value)


class Client:
    def __init__(self, hook=None): self.calls=[]; self.hook=hook; self.closed=False
    def read(self, operation, arguments, *, context):
        context.begin_operation(); self.calls.append((operation,arguments))
        value = self.hook(operation,arguments) if self.hook else None
        if isinstance(value,Exception): raise value
        if value is None: value = {'list':[row()], 'total':1} if operation=='list' else row(arguments['id'])
        return AlphapaiResponse(value, NOW)
    def close(self): self.closed=True


def registry(client=None, profile=PROFILE):
    reg=MaterialRegistry(); reg.register(AlphapaiMaterialAdapter(profile,client=client or Client()));return reg


def test_profile_opt_in_existing_env_names_and_secret_free_status(tmp_path):
    values={'ALPHA_PIE_PHONE':PROFILE.phone,'ALPHA_PIE_PWD':PROFILE.password}
    assert alphapai_profile(values=values) is None
    values['ALPHAPAI_MATERIALS_ENABLED']='true'
    profile=alphapai_profile(values=values)
    assert profile.phone==PROFILE.phone and profile.password==PROFILE.password
    assert PROFILE.phone not in repr(profile) and PROFILE.password not in repr(profile)
    path=tmp_path/'credentials.env';path.write_text('\n'.join(f'{k}={v}' for k,v in values.items()));path.chmod(0o600)
    caps=list_material_capabilities(registry=build_material_registry(env_file=path))
    assert caps['capabilities'][0]['provider']=='alphapai'
    status=source_configuration_status(env_file=path)
    record=next(s for s in status['sources'] if s['provider']=='alphapai')
    assert record['configured'] and not record['requires_api_key'] and not record['live_verified']
    assert PROFILE.phone not in json.dumps(status) and PROFILE.password not in json.dumps(status)


@pytest.mark.parametrize('key,value', [('ALPHAPAI_MATERIALS_ENABLED','yes'), ('ALPHA_PIE_PHONE','not-a-phone'),
    ('ALPHA_PIE_PWD',''),('ALPHAPAI_MAX_PAGES','4'),('ALPHAPAI_MAX_CALLS','0'),
    ('ALPHAPAI_CACHE_TTL_SECONDS','-1'),('ALPHAPAI_CACHE_DIR','relative'),('ALPHAPAI_BROWSER_EXECUTABLE','relative')])
def test_invalid_profile_is_safe(key,value):
    values={'ALPHAPAI_MATERIALS_ENABLED':'true','ALPHA_PIE_PHONE':PROFILE.phone,'ALPHA_PIE_PWD':PROFILE.password,key:value}
    with pytest.raises(SourceConfigError): alphapai_profile(values=values)


@pytest.mark.parametrize('value', ['alphapie://meeting/'+ID+'/summary','https://alphapai-web.rabyte.cn/'+ID,
    'alphapai://meeting/short/summary', 'alphapai://meeting/'+ID+'/summary?token=secret',
    'alphapai://meeting/'+ID+'/audio','alphapai://meeting/'+ID+'/summary#x','alphapai://meeting/../summary'])
def test_only_explicit_credential_free_shared_meeting_refs(value):
    with pytest.raises(DataAdapterError): _parse_reference(value)


def test_date_payload_and_read_only_allowlist():
    args={'query':'茅台', 'start':'2026-09-01','end':'2026-09-18','size':5,'page':1,'symbols':['600519.SH']}
    _validate('list',args);_validate('detail',{'id':ID})
    assert _parse_reference(_reference(ID,'transcript'))==(ID,'transcript')
    payload=_payload(args)
    assert payload['beginTime']=='2026-09-01 00:00:00' and payload['endTime']=='2026-09-18 23:59:59'
    assert payload['marketTypeV2']=='' and payload['stock']==['600519.SH']
    for op,a in [('qa',args),('detail',{'id':ID,'url':'https://evil.example'}),('list',{**args,'size':51}),
            ('list',{**args,'start':'2026-09-01 00:00:00'}),('list',{**args,'query':'bad\nquery'}),('list',{**args,'page':True})]:
        with pytest.raises(DataAdapterError): _validate(op,a)


@pytest.mark.parametrize('http,body,expected',[(200,{'code':500000,'message':'系统繁忙'},'network'),
    (200,{'code':400000,'message':'今日查看上限'},'quota'),(200,{'message':'请登录'},'authentication_failed'),
    (200,{'message':'无权限'},'entitlement_denied'),(429,{},'rate_limit'),(302,{},'blocked_url'),
    (200,{'message':'滑块验证'},'alphapai_login_challenge'),(200,{'message':'不存在'},'not_found')])
def test_business_errors_are_not_empty_success(http,body,expected):
    assert _error_code(http,body)==expected
    with pytest.raises(DataAdapterError,match=expected):_checked_reply({'http':http,'body':body},'detail',{'id':ID})


def test_reply_schema_secret_echo_and_account_fields_rejected_or_removed():
    raw={'http':200,'body':{'code':200000,'data':row(uid='private_uid',originMediaUrl='https://secret.invalid')}}
    result=_checked_reply(raw,'detail',{'id':ID})
    assert 'uid' not in result and 'originMediaUrl' not in result
    rotated=_checked_reply(raw,'detail',{'id':'other_identifier_00001'})
    assert rotated['_requested_id']=='other_identifier_00001' and rotated['id']==ID
    with pytest.raises(DataAdapterError):_checked_reply({'http':200,'body':{'code':200000,'data':row(id='bad')}},'detail',{'id':ID})
    with pytest.raises(DataAdapterError):_checked_reply(raw,'detail',{'id':ID},('private_uid',))
    for data in ({'list':[]}, {'list':[row()]*2,'total':2}, {'list':{},'total':0}, {'list':[],'total':False}):
        with pytest.raises(DataAdapterError):_checked_reply({'http':200,'body':{'code':200000,'data':data}},'list',{'size':1})


def test_summary_search_provenance_scope_dates_and_exact_citations():
    client=Client();result=search_materials(request(),registry=registry(client))
    assert result.items
    version=result.items[0]['versions'][0]
    assert version['text_scope']=='abstract' and version['provenance']['generated']
    assert version['provenance']['evidence_type']=='opinion' and version['original_url'] is None
    assert version['provenance']['source_tier']=='MEDIA'
    assert version['read_details']['publisher_identity_verification']=='provider_label_only'
    assert version['read_details']['complete'] is False
    assert version['read_details']['meeting_at']=='2026-09-11T15:00:00+08:00'
    assert version['published_at']=='2026-09-11T15:54:08+08:00'
    assert version['read_details']['available_text_references']['transcript']==_reference(ID,'transcript')
    assert all(s['text']==version[s['source_part']][s['start_char']:s['end_char']] for s in version['evidence_spans'])
    assert not result.coverage[0]['source_page_complete']
    assert [op for op,a in client.calls]==['list','detail']


def test_highlight_tags_and_rotated_detail_ids_preserve_request_identity():
    client=Client(lambda op,a:{'list':[row(title="茅台<span class='highlight'>渠道</span>经销商交流")],'total':1}
        if op=='list' else row('rotated_identifier_000000',_requested_id=ID))
    result=search_materials(request(),registry=registry(client))
    version=result.items[0]['versions'][0]
    assert version['source_ref']==_reference(ID) and version['text_scope']=='abstract'
    assert version['read_details']['provider_identifier_rotated']


def test_dry_run_and_zero_read_budget_do_not_consume_details():
    client=Client()
    result=search_materials(request(dry_run=True),registry=registry(client))
    assert not client.calls and not result.items
    result=search_materials(request(text_reads_per_source=0),registry=registry(client))
    assert [op for op,a in client.calls]==['list']
    assert result.items[0]['versions'][0]['text_scope']=='search_snippet'


def test_summary_guard_does_not_accept_generated_full_original():
    reg=registry();reg.entries()[0].capability=replace(reg.entries()[0].capability,allows_stored_summaries=False)
    result=search_materials(request(),registry=reg)
    assert not result.items and any(d.code=='invalid_material_response' for d in result.diagnostics)


def test_outside_dates_and_unknown_publication_not_invented():
    client=Client(lambda op,a:{'list':[row(date=None),row('second_identifier_0001',date='2026-08-31 23:59:59')],'total':2})
    result=search_materials(request(),registry=registry(client))
    assert not result.items and all(op=='list' for op,a in client.calls)
    assert any(d.code=='alphapai_publication_unknown_or_outside_window' for d in result.diagnostics)


def test_partial_detail_error_keeps_snippet_and_stops_on_quota():
    client=Client(lambda op,a:DataAdapterError('quota') if op=='detail' else None)
    result=search_materials(request(),registry=registry(client))
    assert result.items[0]['versions'][0]['text_scope']=='search_snippet'
    assert any(d.code=='quota' for d in result.diagnostics)
    client=Client(lambda op,a:DataAdapterError('authentication_failed'))
    result=search_materials(request(),registry=registry(client))
    assert not result.items and any(d.code=='authentication_failed' for d in result.diagnostics)


def test_rotation_and_identical_metadata_are_not_silent_confirmations():
    client=Client(lambda op,a:{'list':[row('identifier_'+str(a['page'])+'_00000000')], 'total':100} if op=='list' else None)
    result=search_materials(request(candidates_per_source=25,text_reads_per_source=0),registry=registry(client))
    assert len(client.calls)==2 and len(result.items)==1
    assert any(d.code=='alphapai_ambiguous_repeated_metadata' for d in result.diagnostics)
    assert any(d.code=='alphapai_pagination_stalled' for d in result.diagnostics)


def test_page_size_does_not_shrink_and_overfetch_is_visible():
    def hook(op,a):
        if op=='list':return {'list':[row(f'identifier_{a["page"]:02}_{i:016}',title=f'茅台动销会议{a["page"]}-{i}') for i in range(20)],'total':60}
    client=Client(hook)
    result=search_materials(request(candidates_per_source=25,text_reads_per_source=0),registry=registry(client))
    assert [a['size'] for op,a in client.calls]==[20,20]
    assert result.coverage[0]['scanned_count']==25
    scans=result.coverage[0]['scans'];assert scans[1]['received_count']==20 and scans[1]['inspected_count']==5


def test_transcript_json_offsets_roles_raw_times_and_truncation():
    reference=_reference(ID,'transcript')
    doc=fetch_alphapai_document(reference,profile=PROFILE,client=Client(),context=RequestContext())
    assert doc.text=='茅台动销情况。\n这是部分转录。'
    details=doc.extra['material_read']
    assert details['machine_transcribed'] and details['complete'] is False
    assert details['source_time_unit']=='unverified' and not doc.extra['generated']
    assert details['provider_word_count']==10000
    assert all(doc.text[s['start_char']:s['end_char']] for s in details['segments'])
    short=fetch_alphapai_document(reference,profile=PROFILE,client=Client(),context=RequestContext(),max_chars=3)
    assert short.text=='茅台动' and 'text_truncated' in short.warnings
    assert short.extra['material_read']['segments'][0]['end_char']==3
    assert _transcript('<p>部分转录</p>',20)[3]=='provider_unstructured_transcript'


@pytest.mark.parametrize('content',['[bad json]', '[{"content":"text","bg":true,"ed":2}]',
    '[{"content":"text","bg":5,"ed":2}]','[{}]','[]',''])
def test_bad_or_empty_transcripts_never_fall_back_to_summary(content):
    with pytest.raises(DataAdapterError):_transcript(content,100)


def test_retrieve_sdk_mcp_routing_summary_and_transcript(monkeypatch,tmp_path):
    from ir_search.infrastructure import alphapai_documents
    original=fetch_alphapai_document
    monkeypatch.setattr(alphapai_documents,'fetch_alphapai_document',lambda ref,**kw:original(ref,profile=PROFILE,client=Client(),**kw))
    result=retrieve(MaterialRequest('茅台',(_reference(ID),_reference(ID,'transcript')),archive_dir=str(tmp_path/'archive')))
    assert len(result.materials)==2
    summary,transcript=result.materials
    assert summary.text_origin=='provider_summary' and summary.provenance.generated
    assert summary.archive['status']=='ok'
    assert transcript.text_origin=='source_excerpt' and not transcript.provenance.generated
    assert transcript.evidence_spans[0]['extra']['source_time_unit']=='unverified'
    for material in result.materials:
        assert material.text_provider=='alphapai'
        assert all(s['text']==material.text[s['start_char']:s['end_char']] for s in material.evidence_spans)
    payload=mcp_server.retrieve_payload('茅台',[_reference(ID,'transcript')]);json.dumps(payload,allow_nan=False)
    assert payload['materials'][0]['read_details']['complete'] is False
    payload=mcp_server.search_materials_payload(request().to_dict(),registry=registry());json.dumps(payload,allow_nan=False)
    assert payload['items']


def test_missing_config_scope_and_cancellation(monkeypatch):
    with pytest.raises(DataAdapterError,match='no_credential'):fetch_alphapai_document(_reference(ID),context=RequestContext())
    with pytest.raises(DataAdapterError,match='entitlement_denied'):
        fetch_alphapai_document(_reference(ID),context=RequestContext(account_scope='other'),profile=PROFILE)
    context=RequestContext();context.cancel()
    result=search_materials(request(),registry=registry(),context=context)
    assert any(d.code=='cancelled' for d in result.diagnostics)
    with pytest.raises(ValueError):fetch_alphapai_document(_reference(ID),context=RequestContext(),max_chars=0)


class Session:
    def __init__(self,profile,context):self.closed=False;self.calls=[]
    def read(self,op,args,context):
        self.calls.append(op)
        return {'http':200,'body':{'code':200000,'data':row(args.get('id',ID)) if op=='detail' else {'list':[row()],'total':1}}}
    def close(self):self.closed=True


def test_client_reuses_login_cache_without_browser_and_enforces_budget(tmp_path):
    profile=replace(PROFILE,cache_ttl_seconds=3600,cache_dir=str(tmp_path/'cache'),max_calls=2)
    sessions=[]
    def factory(p,c):session=Session(p,c);sessions.append(session);return session
    client=AlphapaiClient(profile,session_factory=factory)
    ctx=RequestContext()
    first=client.read('detail',{'id':ID},context=ctx);second=client.read('detail',{'id':ID},context=ctx)
    assert len(sessions)==1 and len(sessions[0].calls)==1 and ctx.operations==2
    assert first.cache_state=='fresh' and second.cache_state=='hit' and first.fetched_at==second.fetched_at
    with pytest.raises(DataAdapterError,match='alphapai_call_budget_exhausted'):client.read('detail',{'id':ID},context=ctx)
    client.close();assert sessions[0].closed
    cached=AlphapaiClient(profile,session_factory=lambda *a:pytest.fail('cached read must not launch browser'))
    assert cached.read('detail',{'id':ID},context=RequestContext()).cache_state=='hit'
    cached.close()


def test_cache_credentials_isolation_tamper_expiration_and_permissions(tmp_path):
    profile=replace(PROFILE,cache_ttl_seconds=3600,cache_dir=str(tmp_path/'cache'))
    cache=_DetailCache(profile);response=AlphapaiResponse(row(),NOW);cache.put(ID,response)
    assert cache.get(ID).data==row()
    file=next((tmp_path/'cache').glob('*.json'))
    assert file.stat().st_mode & 0o077==0
    assert PROFILE.phone not in file.read_text() and PROFILE.password not in file.read_text()
    assert _DetailCache(replace(profile,password='different_password')).get(ID) is None
    cache.put(ID,AlphapaiResponse(row(),NOW-timedelta(hours=2)));assert cache.get(ID) is None
    cache.put(ID,response);data=json.loads(file.read_text());data['record']['data']['title']='tampered';file.write_text(json.dumps(data))
    with pytest.raises(DataAdapterError,match='alphapai_cache_invalid'):cache.get(ID)
    file.unlink();target=tmp_path/'outside';target.write_text('{}');file.symlink_to(target)
    with pytest.raises(DataAdapterError):cache.get(ID)


def test_cache_unsafe_directory_and_capacity_bound(tmp_path):
    root=tmp_path/'cache';root.mkdir(mode=0o755)
    cache=_DetailCache(replace(PROFILE,cache_ttl_seconds=3600,cache_dir=str(root)))
    with pytest.raises(DataAdapterError,match='alphapai_cache_unavailable'):cache.get(ID)
    root.chmod(0o700)
    for i in range(202):(root/f'{i:064x}.json').write_text('{}')
    cache.put(ID,AlphapaiResponse(row(),NOW))
    assert len(list(root.glob('*.json')))==200


def test_operation_budget_before_login_and_failure_never_reauthenticates():
    client=AlphapaiClient(PROFILE,session_factory=lambda *args:pytest.fail('no budget for login'))
    with pytest.raises(RequestStopped,match='operation_budget_exhausted'):
        client.read('detail',{'id':ID},context=RequestContext(max_operations=1))
    sessions=[]
    class FailedSession(Session):
        def read(self,*args): raise DataAdapterError('quota')
    def factory(*args):
        session=FailedSession(*args);sessions.append(session);return session
    client=AlphapaiClient(PROFILE,session_factory=factory)
    for _ in range(2):
        with pytest.raises(DataAdapterError,match='quota'):client.read('detail',{'id':ID},context=RequestContext())
    assert len(sessions)==1 and sessions[0].closed


def test_live_text_survives_cache_write_failure():
    class FailedCache:
        def get(self,*a):return None
        def put(self,*a):raise DataAdapterError('alphapai_cache_unavailable')
    client=AlphapaiClient(PROFILE,session_factory=Session,cache=FailedCache())
    doc=fetch_alphapai_document(_reference(ID),profile=PROFILE,client=client,context=RequestContext())
    assert doc.text and doc.extra['material_read']['cache_state']=='write_failed'
    assert 'alphapai_cache_write_failed' in doc.warnings
    client.close()


def test_detail_identity_mismatch_keeps_unread_list_evidence():
    client=Client(lambda op,a:row(title='另一场会议') if op=='detail' else None)
    result=search_materials(request(),registry=registry(client))
    assert result.items[0]['versions'][0]['text_scope']=='search_snippet'
    assert any(d.code=='upstream_schema' for d in result.diagnostics)


def test_transcript_alternate_is_explicit_and_no_summary_substitution():
    data=row(mtSummary=None,mtSummarySwitchOpen=row()['mtSummary'])
    doc=_document(_reference(ID,'transcript'),AlphapaiResponse(data,NOW),1000)
    assert doc.extra['material_read']['source_field']=='mtSummarySwitchOpen.content'
    assert doc.extra['material_read']['available_text_references']['transcript']==_reference(ID,'transcript')
    with pytest.raises(DataAdapterError,match='no_extracted_text'):
        _document(_reference(ID,'transcript'),AlphapaiResponse(row(mtSummary=None),NOW),1000)


def test_worker_missing_dependency_emits_only_safe_error(monkeypatch,capsys):
    from ir_search.infrastructure import _alphapai_worker
    import builtins
    original=builtins.__import__
    def imports(name,*args,**kwargs):
        if name=='playwright.sync_api':raise ImportError('sensitive import details')
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',imports)
    previous=os.umask(0o077)
    try:_alphapai_worker.main()
    finally:os.umask(previous)
    assert json.loads(capsys.readouterr().out)=={'error':'browser_dependency_missing'}


def test_worker_protocol_rejects_oversized_output_and_arbitrary_error_text():
    from ir_search.infrastructure.alphapai import _BrowserSession, MAX_BYTES
    import io,queue
    from types import SimpleNamespace
    session=object.__new__(_BrowserSession)
    session._events=queue.Queue(maxsize=2)
    session._process=SimpleNamespace(stdout=io.BytesIO(b'x'*(MAX_BYTES+1)),stdin=io.BytesIO())
    session._read()
    assert session._events.get()['error']=='response_too_large'
    session._events.put({'error':PROFILE.password})
    with pytest.raises(DataAdapterError,match='browser_failed'):
        session._exchange({},RequestContext())


def test_worker_environment_does_not_inherit_other_credentials(monkeypatch,tmp_path):
    from ir_search.infrastructure.web_browser import _environment
    monkeypatch.setenv('IR_SEARCH_CREDENTIALS_FILE','private-env-path')
    monkeypatch.setenv('ALPHA_PIE_PWD',PROFILE.password)
    monkeypatch.setenv('OTHER_TOKEN','private-value')
    monkeypatch.setenv('PYTHONPATH','malicious-path')
    child=_environment(str(tmp_path))
    assert not {'IR_SEARCH_CREDENTIALS_FILE','ALPHA_PIE_PWD','OTHER_TOKEN','PYTHONPATH'} & set(child)


def test_factory_browser_is_closed_when_adapter_owns_it(monkeypatch):
    from ir_search.adapters import alphapai_materials
    fake=Client()
    monkeypatch.setattr(alphapai_materials,'AlphapaiClient',lambda p:fake)
    adapter=AlphapaiMaterialAdapter(PROFILE)
    adapter.search_materials(request(),context=RequestContext())
    assert fake.closed


def test_reader_owns_and_closes_client_on_failure(monkeypatch):
    from ir_search.infrastructure import alphapai_documents
    fake=Client(lambda *a:DataAdapterError('quota'))
    monkeypatch.setattr(alphapai_documents,'AlphapaiClient',lambda p:fake)
    with pytest.raises(DataAdapterError,match='quota'):
        fetch_alphapai_document(_reference(ID),context=RequestContext(),profile=PROFILE)
    assert fake.closed
