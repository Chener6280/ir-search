"""Offline synthetic contracts; never load the user's account or browser state."""
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

import pytest

from ir_search import MaterialRequest, MaterialSearchRequest, RequestContext, retrieve, search_materials
from ir_search.context import RequestStopped
from ir_search.contracts.materials import MaterialKind
from ir_search.infrastructure.credentials import SourceConfigError, source_configuration_status
from ir_search.infrastructure.gangtise import (GangtiseProfile, GangtiseClient, GangtiseResponse, gangtise_profile,
    _reference, _parse_reference, _request, _checked_reply, _error_code, _file_path)
from ir_search.infrastructure.gangtise_auth import _State, _get_token, _invalidate_token, authenticate_gangtise
from ir_search.infrastructure.gangtise_documents import fetch_gangtise_document, _time
from ir_search.adapters.gangtise_materials import GangtiseMaterialAdapter
from ir_search.material_registry import MaterialRegistry, build_material_registry, list_material_capabilities
from ir_search.registry import DataAdapterError

PROFILE = GangtiseProfile('10000000000', 'synthetic_gangtise_password')
NOW = datetime.now(timezone.utc)
DATE = '2026-09-11 15:00:00'


def row(category='summary', identifier='123', **kw):
    result = {'id':identifier, 'title':'茅台渠道动销交流', 'msgTime':DATE, 'pubTime':DATE,
        'partyName':'某机构', 'brief':'茅台动销搜索片段', 'msgText':[{'usage':10,'url':'minutes/synthetic.txt'}]}
    if category == 'report': result.update(rptId=identifier, title='茅台研究报告', brief='茅台动销研报摘要')
    if category == 'opinion': result.update(title=None, msgText={'title':'茅台研究观点','content':'茅台动销的供应商研究观点。'})
    result.update(kw); return result


def request(**kw):
    args = dict(question='茅台动销', entities=('茅台',), providers=('gangtise',),
        published_start='2026-09-01', published_end='2026-09-18', candidates_per_source=6, text_reads_per_source=3)
    args.update(kw); return MaterialSearchRequest(**args)


class Client:
    def __init__(self, hook=None): self.hook=hook; self.calls=[]; self.closed=False
    def read(self, operation, args, *, context):
        context.begin_operation(); self.calls.append((operation,args))
        data = self.hook(operation,args) if self.hook else None
        if isinstance(data, Exception): raise data
        if data is None:
            data = {'list':[row(args['category'])], 'total':1} if operation=='search' else row(args['category'], args['id'])
        return GangtiseResponse(data, NOW)
    def _summary_text(self, identifier, path, *, context):
        context.begin_operation(); self.calls.append(('download',{'id':identifier}))
        return '<p>茅台渠道动销的已有 AI 纪要摘要。</p>'
    def close(self): self.closed=True


def registry(client=None, profile=PROFILE):
    result=MaterialRegistry(); result.register(GangtiseMaterialAdapter(profile, client=client or Client()));return result


def test_profile_opt_in_aliases_registry_and_secret_free_health(tmp_path):
    values={'GANTISE_PHONE':PROFILE.phone,'GANTISE_PWD':PROFILE.password}
    assert gangtise_profile(values=values) is None
    values['GANGTISE_MATERIALS_ENABLED']='true'
    profile=gangtise_profile(values=values)
    assert profile.phone==PROFILE.phone and profile.password==PROFILE.password
    assert PROFILE.phone not in repr(profile) and PROFILE.password not in repr(profile)
    canonical={'GANGTISE_MATERIALS_ENABLED':'true','GANGTISE_PHONE':PROFILE.phone,'GANGTISE_PASSWORD':PROFILE.password}
    assert gangtise_profile(values=canonical)==profile
    path=tmp_path/'sources.env';path.write_text('\n'.join(k+'='+v for k,v in values.items()));path.chmod(0o600)
    caps=list_material_capabilities(registry=build_material_registry(env_file=path))
    assert caps['capabilities'][0]['provider']=='gangtise'
    status=source_configuration_status(env_file=path)
    record=next(s for s in status['sources'] if s['provider']=='gangtise')
    assert record['configured'] and not record['requires_api_key'] and not record['live_verified']
    assert PROFILE.phone not in json.dumps(status) and PROFILE.password not in json.dumps(status)


@pytest.mark.parametrize('key,value', [('GANGTISE_MATERIALS_ENABLED','yes'), ('GANTISE_PHONE','bad'),
    ('GANTISE_PWD',''),('GANGTISE_MAX_PAGES','4'),('GANGTISE_MAX_CALLS','0'),
    ('GANGTISE_STATE_DIR','relative'),('GANGTISE_BROWSER_EXECUTABLE','relative')])
def test_invalid_configuration_fails_closed(key,value):
    values={'GANGTISE_MATERIALS_ENABLED':'true','GANTISE_PHONE':PROFILE.phone,'GANTISE_PWD':PROFILE.password,key:value}
    with pytest.raises(SourceConfigError):gangtise_profile(values=values)


@pytest.mark.parametrize('value',['gantise://summary/123','gangtise://summary/../secrets','gangtise://summary/123?token=x',
    'gangtise://summary/123#fragment','gangtise://file/123','https://open.gangtise.com/summary/123'])
def test_opaque_reference_validation(value):
    with pytest.raises(DataAdapterError):_parse_reference(value)


def test_request_allowlist_zero_based_pagination_and_distinct_report_id():
    for category in ('summary','report','opinion'):
        assert _parse_reference(_reference(category,123))==(category,'123')
        path,payload=_request('search',{'category':category,'query':'茅台','page':2,'size':3})
        assert path.endswith('/keysearch/global/search') and payload['pageNum']==1 and payload['filters']['sort']==2
    assert _request('detail',{'category':'opinion','id':'123'})[1]['condition']['must']=={'id':'123'}
    for op,args in [('delete',{'category':'summary','id':'123'}),('detail',{'category':'summary','id':'1?token=x'}),
        ('search',{'category':'report','query':'x','page':True,'size':1}),
        ('search',{'category':'report','query':'x\ny','page':1,'size':1}),
        ('search',{'category':'report','query':'x','page':1,'size':21}),
        ('detail',{'category':'summary','id':'1','url':'https://evil.invalid'})]:
        with pytest.raises(DataAdapterError):_request(op,args)


@pytest.mark.parametrize('status,body,expected',[(200,{'code':'899999'},'gangtise_login_challenge'),
    (200,{'code':'800009'},'gangtise_login_challenge'),(200,{'code':10011401},'entitlement_denied'),
    (200,{'code':10011402},'entitlement_denied'),(200,{'code':903301},'quota'),
    (200,{'code':'910000'},'authentication_failed'),(401,{},'authentication_failed'),(403,{},'entitlement_denied'),
    (429,{},'rate_limit'),(302,{},'blocked_url'),(500,{},'network')])
def test_business_denials_are_not_empty_success(status,body,expected):
    assert _error_code(status,body)==expected
    with pytest.raises(DataAdapterError,match=expected):_checked_reply({'http':status,'body':body},'detail',{'category':'summary','id':'123'})


def reply(data, code='000000'):return {'http':200,'body':{'status':True,'code':code,'data':data}}


def test_schema_removes_account_fields_and_links_and_binds_detail_identity():
    r=row(phone='private_phone',token='private_token',url='https://signed.invalid/?token=private_token')
    clean=_checked_reply(reply(r),'detail',{'category':'summary','id':'123'})
    assert not {'phone','token','url'} & set(clean)
    with pytest.raises(DataAdapterError):_checked_reply(reply(r),'detail',{'category':'summary','id':'456'})
    with pytest.raises(DataAdapterError):_checked_reply(reply(r),'detail',{'category':'summary','id':'123'},('private_token',))
    r=row('report',id=999,rptId='123')
    assert _checked_reply(reply([r],10010000),'detail',{'category':'report','id':'123'})['id']==999
    opinion=row('opinion',msgText=json.dumps({'content':'观点','url':'https://signed.invalid'}))
    assert _checked_reply(reply([opinion],10010000),'detail',{'category':'opinion','id':'123'})['msgText']=={'content':'观点'}
    with pytest.raises(DataAdapterError):_checked_reply(reply(row(), code='new_unknown_code'),'detail',{'category':'summary','id':'123'})
    with pytest.raises(DataAdapterError):_checked_reply(reply(row(), code=10011401),'detail',{'category':'summary','id':'123'})


@pytest.mark.parametrize('group',[{'srcCode':'WRONG','list':[],'total':0},
    {'srcCode':'SUMMARY','list':[row(),row()],'total':2},{'srcCode':'SUMMARY','list':[],'total':False},
    {'srcCode':'SUMMARY','list':'wrong','total':1}])
def test_search_shape_strict(group):
    with pytest.raises(DataAdapterError):_checked_reply(reply([group]),'search',{'category':'summary','size':1})


def test_source_fields_and_unverified_time_do_not_claim_official_facts():
    client=Client();result=search_materials(request(),registry=registry(client))
    assert len(result.items)==3 and result.status.value=='partial'
    versions=[v for group in result.items for v in group['versions']]
    summary=next(v for v in versions if v['source_ref'].startswith('gangtise://summary/'))
    assert summary['text_scope']=='abstract' and summary['provenance']['generated']
    assert summary['provenance']['source_tier']=='MEDIA' and summary['provenance']['evidence_type']=='opinion'
    assert summary['read_details']['complete'] is False
    assert 'gangtise_display_time_not_verified_publication_time' in summary['warnings']
    assert {v['text_scope'] for v in versions}=={'abstract','source_excerpt'}
    for v in versions:
        for span in v['evidence_spans']:
            assert span['text']==v[span['source_part']][span['start_char']:span['end_char']]
    assert not result.complete


def test_round_robin_scan_budget_and_constant_page_size():
    client=Client(lambda op,a:{'list':[row(a['category'],str(a['page']*10+i)) for i in range(a['size'])], 'total':99})
    result=GangtiseMaterialAdapter(PROFILE,client=client).search_materials(request(candidates_per_source=5,text_reads_per_source=0),context=RequestContext())
    assert result.scanned_count==5 and len(client.calls)==5
    assert [a['category'] for op,a in client.calls][:3]==['summary','report','opinion']
    assert all(a['size']==1 for op,a in client.calls)
    assert any(d.code=='gangtise_scan_limit' for d in result.diagnostics)


def test_stalled_and_wrong_date_pages_have_diagnostics():
    client=Client(lambda op,a:{'list':[row(a['category'],msgTime='2026-08-01',pubTime='2026-08-01')],'total':100})
    result=GangtiseMaterialAdapter(PROFILE,client=client).search_materials(request(),context=RequestContext())
    assert not result.candidates and any(d.code=='gangtise_pagination_stalled' for d in result.diagnostics)
    assert any(d.code=='gangtise_publication_unknown_or_outside_window' for d in result.diagnostics)


def test_zero_read_dry_run_and_material_filter():
    client=Client();reg=registry(client)
    assert not search_materials(request(dry_run=True),registry=reg).items and not client.calls
    result=search_materials(request(text_reads_per_source=0,material_types=('research_report',)),registry=reg)
    assert len(client.calls)==1 and client.calls[0][1]['category']=='report'
    assert result.items[0]['versions'][0]['text_scope']=='search_snippet'


def test_authentication_stops_all_categories_but_category_entitlement_does_not():
    client=Client(lambda op,a:DataAdapterError('gangtise_login_challenge'))
    result=search_materials(request(),registry=registry(client))
    assert not result.items and len(client.calls)==1
    assert any(d.code=='gangtise_login_challenge' for d in result.diagnostics)
    client=Client(lambda op,a:DataAdapterError('entitlement_denied') if a['category']=='summary' else None)
    result=search_materials(request(),registry=registry(client))
    assert len(result.items)==2 and any(d.code=='entitlement_denied' for d in result.diagnostics)


def test_detail_mismatch_and_quota_preserve_snippet_with_diagnostic():
    client=Client(lambda op,a:row(a['category'],title='different') if op=='detail' else None)
    result=search_materials(request(),registry=registry(client))
    assert any(d.code=='upstream_schema' for d in result.diagnostics)
    assert all(v['text_scope']=='search_snippet' for g in result.items for v in g['versions'])
    client=Client(lambda op,a:DataAdapterError('quota') if op=='detail' else None)
    result=search_materials(request(),registry=registry(client))
    assert sum(op=='detail' for op,a in client.calls)==1 and any(d.code=='quota' for d in result.diagnostics)


def test_normalization_prioritizes_human_minutes_and_report_is_only_abstract():
    client=Client(lambda op,a:row(msgText=[{'usage':10,'url':'ai.txt'},{'usage':5,'url':'human.txt'}]))
    doc=fetch_gangtise_document('gangtise://summary/123',profile=PROFILE,client=client,context=RequestContext())
    assert not doc.extra['generated'] and doc.extra['material_read']['provider_usage']==5
    doc=fetch_gangtise_document('gangtise://report/123',profile=PROFILE,client=Client(),context=RequestContext(),max_chars=5)
    assert len(doc.text)==5 and 'text_truncated' in doc.warnings and 'gangtise_report_pdf_not_read' in doc.warnings
    assert doc.extra['material_read']['content_origin']=='provider_stored_summary'


def test_document_does_not_silently_fallback_when_only_pdf_or_denied():
    client=Client(lambda op,a:row(msgText=[{'usage':10,'url':'unsupported.pdf'}]))
    with pytest.raises(DataAdapterError,match='gangtise_format_unavailable'):
        fetch_gangtise_document('gangtise://summary/123',profile=PROFILE,client=client,context=RequestContext())
    with pytest.raises(ValueError):fetch_gangtise_document('gangtise://summary/123',max_chars=0,context=RequestContext())
    with pytest.raises(DataAdapterError,match='no_credential'):fetch_gangtise_document('gangtise://summary/123',context=RequestContext())
    with pytest.raises(DataAdapterError,match='entitlement_denied'):
        fetch_gangtise_document('gangtise://summary/123',profile=PROFILE,context=RequestContext(account_scope='other'))


def test_retrieve_dispatch_and_exact_citations(monkeypatch):
    def fetch(ref,**kw):return fetch_gangtise_document(ref,profile=PROFILE,client=Client(),**kw)
    monkeypatch.setattr('ir_search.infrastructure.gangtise_documents.fetch_gangtise_document',fetch)
    result=retrieve(MaterialRequest(urls=('gangtise://summary/123','gangtise://report/123','gangtise://opinion/123'),question='茅台动销'),context=RequestContext(max_operations=20))
    assert len(result.materials)==3
    assert {m.text_origin for m in result.materials}=={'provider_summary','source_excerpt'}
    assert all(m.text_provider=='gangtise' and not m.read_details['complete'] for m in result.materials)
    for m in result.materials:
        assert all(s['text']==m.text[s['start_char']:s['end_char']] for s in m.evidence_spans)


@pytest.mark.parametrize('value',['../secret.txt','https://evil.invalid/file.txt','file.txt?token=secret','//evil/x.txt',
    'a/%2e%2e/file.txt','a/%252e%252e/file.txt','a%00.txt','file.pdf','file.txt#x','file.txt&x=y','a\\secret.txt'])
def test_summary_download_paths_cannot_escape_or_carry_credentials(value):
    with pytest.raises(DataAdapterError):_file_path(value)


def test_client_session_call_budget_and_bound_download():
    calls=[];auth=[]
    def transport(path,payload,token,**kw):
        calls.append(path)
        if kw.get('text'):return '<p>纪要文字</p>'
        return reply(row())
    client=GangtiseClient(replace(PROFILE,max_calls=2),transport=transport,
        authenticator=lambda p,**kw:auth.append(True) or 'synthetic_account_token')
    ctx=RequestContext(max_operations=10)
    with pytest.raises(DataAdapterError,match='blocked_url'):client._summary_text('123','minutes/synthetic.txt',context=ctx)
    client.read('detail',{'category':'summary','id':'123'},context=ctx)
    assert client._summary_text('123','minutes/synthetic.txt',context=ctx)=='<p>纪要文字</p>'
    assert len(auth)==1 and len(calls)==2
    with pytest.raises(DataAdapterError,match='gangtise_call_budget_exhausted'):
        client.read('detail',{'category':'summary','id':'123'},context=ctx)
    client.close();assert not client._token and not client._files


def test_client_failures_do_not_retry_or_expose_token(tmp_path):
    profile=replace(PROFILE,state_dir=str(tmp_path/'private'))
    calls=[]
    def fail(*a,**kw):calls.append(1);return {'http':429,'body':{}}
    client=GangtiseClient(profile,transport=fail,authenticator=lambda *a,**kw:'synthetic_token')
    for _ in range(2):
        with pytest.raises(DataAdapterError,match='rate_limit'):client.read('detail',{'category':'summary','id':'123'},context=RequestContext())
    assert len(calls)==1
    ctx=RequestContext();ctx.cancel()
    with pytest.raises(RequestStopped,match='cancelled'):client.read('detail',{'category':'summary','id':'123'},context=ctx)
    with pytest.raises(DataAdapterError,match='entitlement_denied'):
        client.read('detail',{'category':'summary','id':'123'},context=RequestContext(account_scope='other'))


def test_client_stops_before_authentication_with_insufficient_budget():
    client=GangtiseClient(PROFILE,authenticator=lambda *a,**kw:pytest.fail('Must not log in'))
    with pytest.raises(RequestStopped,match='operation_budget_exhausted'):
        client.read('detail',{'category':'summary','id':'123'},context=RequestContext(max_operations=1))


def test_private_state_permissions_tampering_rotation_and_nonpersistent_secrets(tmp_path):
    profile=replace(PROFILE,state_dir=str(tmp_path/'state'));state=_State(profile)
    assert state.read()=={}
    state.write({'token':'synthetic_token','saved_at':time.time()})
    assert _get_token(profile,context=RequestContext())=='synthetic_token'
    assert state.root.stat().st_mode & 0o777==0o700
    assert (state.root/'session.json').stat().st_mode & 0o777==0o600
    raw=(state.root/'session.json').read_text()
    assert profile.phone not in raw and profile.password not in raw
    assert _State(replace(profile,password='rotated_password')).read()=={}
    with state.lock():
        with pytest.raises(DataAdapterError,match='gangtise_login_busy'):
            with state.lock():pass
    with state.lock():pass
    _invalidate_token(profile,'another_token');assert state.read().get('token')=='synthetic_token'
    _invalidate_token(profile,'synthetic_token')
    with pytest.raises(DataAdapterError,match='gangtise_login_challenge'):_get_token(profile,context=RequestContext())
    state.write({'token':'synthetic_token','saved_at':time.time()})
    path=state.root/'session.json';path.write_text(raw.replace('synthetic_token','modified_token'))
    with pytest.raises(DataAdapterError,match='gangtise_state_invalid'):state.read()


def test_state_symlinks_insecure_permissions_and_oversized_files(tmp_path):
    state=_State(replace(PROFILE,state_dir=str(tmp_path/'state')));state.prepare()
    target=tmp_path/'outside';target.write_text('{}');(state.root/'session.json').symlink_to(target)
    with pytest.raises(DataAdapterError):state.read()
    (state.root/'session.json').unlink();state.write({'token':'synthetic_token'})
    path=state.root/'session.json';path.chmod(0o644)
    with pytest.raises(DataAdapterError):state.read()
    path.chmod(0o600);path.write_text('x'*33000)
    with pytest.raises(DataAdapterError):state.read()
    link=tmp_path/'link';link.symlink_to(state.root.parent)
    with pytest.raises(DataAdapterError):_State(replace(PROFILE,state_dir=str(link))).prepare()


def test_authentication_helper_cli_and_expired_state(monkeypatch,tmp_path,capsys):
    import ir_search.infrastructure.gangtise_auth as auth
    profile=replace(PROFILE,state_dir=str(tmp_path/'state'));calls=[]
    monkeypatch.setattr(auth,'_browser_login',lambda p,s,**kw:calls.append(kw) or 'synthetic_token')
    result=authenticate_gangtise(profile=profile,timeout=30)
    assert result['authentication']=='completed' and not result['material_access_verified']
    assert calls[-1]['interactive'] and 'synthetic_token' not in json.dumps(result)
    state=_State(profile);state.write({'token':'old_token','saved_at':time.time()-4000})
    assert _get_token(profile,context=RequestContext())=='synthetic_token' and not calls[-1]['interactive']
    with pytest.raises(ValueError):authenticate_gangtise(profile=profile,timeout=10)
    monkeypatch.setattr(auth,'authenticate_gangtise',lambda **kw:result)
    monkeypatch.setattr('sys.argv',['gangtise_auth'])
    assert auth.main()==0 and json.loads(capsys.readouterr().out)['authentication']=='completed'
    def fail(**kw):raise DataAdapterError('gangtise_login_challenge')
    monkeypatch.setattr(auth,'authenticate_gangtise',fail)
    assert auth.main()==1 and 'gangtise_login_challenge' in capsys.readouterr().out


def test_timestamps_unknown_precision_and_bounded_invalid_values():
    assert _time(None) is None
    assert _time('2026-09-11').hour==0
    assert _time('2026-09-11T07:00:00Z')==_time(DATE)
    assert _time(1789110000000)==_time(1789110000)
    for value in (True,'tomorrow',0,float('nan'),'2026-99-01'):
        with pytest.raises(DataAdapterError):_time(value)
