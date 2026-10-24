"""Synthetic platform fixtures: no credentials, browser or live XHS calls."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
import os

import pytest

from _platform import POSIX_PERMISSIONS
from ir_search import MaterialRequest, MaterialSearchRequest, RequestContext, retrieve, search_materials
from ir_search.material_registry import MaterialRegistry, build_material_registry
from ir_search.infrastructure.credentials import SourceConfigError, source_configuration_status
from ir_search.infrastructure.xhs import XhsClient, XhsProfile, XhsResponse, xhs_profile, _reference, _parse_reference, _snapshot
from ir_search.infrastructure.xhs_documents import fetch_xhs_document
from ir_search.adapters.xhs_materials import XhsMaterialAdapter
from ir_search.registry import DataAdapterError

ID='000000000000000000000001'
ID2='000000000000000000000002'
SECRET='synthetic-local-service-token'
ACCESS='synthetic-note-access-token'


def row(identifier=ID):
    return {'id':identifier,'modelType':'note','xsecToken':ACCESS,'noteCard':{
        'type':'normal','displayTitle':'茅台终端动销反馈 '+identifier[-1],
        'user':{'nickname':'合成测试作者'},'interactInfo':{'likedCount':'1.2万','commentCount':'0'}}}


def detail(identifier=ID):
    return {'feed_id':identifier,'data':{'note':{'noteId':identifier,'xsecToken':ACCESS,
        'title':'茅台终端动销反馈 '+identifier[-1],'desc':'茅台终端动销来自单个门店的观察，不能代表整体销量。',
        'time':1789354800000,'type':'normal','user':{'nickname':'合成测试作者'},'imageList':[{}],
        'interactInfo':{'likedCount':'123','collectedCount':'2万+','commentCount':'3'}},
        'comments':{'hasMore':True,'cursor':'private-provider-cursor','list':[
            {'id':'c1','noteId':identifier,'content':'茅台动销需要继续核实。','createTime':1789354800000,
                'userInfo':{'nickname':'合成评论者'},'subComments':[
                    {'id':'c2','noteId':identifier,'content':'茅台动销也有地区差异。','userInfo':{'nickname':'合成回复者'}}]},
            {'id':'c3','noteId':identifier,'content':'茅台库存反馈。','userInfo':{'nickname':'另一评论者'}}]}}}


class Transport:
    def __init__(self): self.rows=[row(),row(ID2)]; self.calls=[]; self.logged=True; self.details={}
    def __call__(self, method, path, payload, *, context):
        context.begin_operation(); self.calls.append((method,path,deepcopy(payload)))
        if path.endswith('/status'): return {'is_logged_in':self.logged,'username':'private-account-name'}
        if path.endswith('/search'): return {'feeds':deepcopy(self.rows)}
        if path.endswith('/detail'):
            value=self.details.get(payload['feed_id'],detail(payload['feed_id']))
            if isinstance(value,Exception): raise value
            return deepcopy(value)
        raise AssertionError(path)


def setup(tmp_path):
    profile=XhsProfile(token=SECRET,cache_dir=str(tmp_path/'xhs'))
    transport=Transport(); client=XhsClient(profile,transport=transport)
    registry=MaterialRegistry();registry.register(XhsMaterialAdapter(profile,client=client))
    return profile,transport,client,registry


def req(**changes):
    return MaterialSearchRequest(**dict(dict(question='茅台动销',keywords=['茅台','动销'],providers=['xhs'],
        published_start='2026-09-01',published_end='2026-09-30',candidates_per_source=2,text_reads_per_source=2),**changes))


def versions(result): return [v for i in result.items for v in i['versions']]


def test_profile_opt_in_no_network_and_secret_repr(tmp_path,monkeypatch):
    assert xhs_profile(values={}) is None
    for values in ({'XHS_MATERIALS_ENABLED':'maybe'},{'XHS_MATERIALS_ENABLED':'true'}):
        with pytest.raises(SourceConfigError):xhs_profile(values=values)
    p=xhs_profile(values={'XHS_MATERIALS_ENABLED':'true','XHS_BACKEND_TOKEN':SECRET,'XHS_CACHE_DIR':'cache'},env_file=tmp_path/'sources.env')
    assert p.cache_dir==str(tmp_path/'cache') and SECRET not in repr(p)
    monkeypatch.setattr(XhsClient,'_request',lambda *a,**k:pytest.fail('health must not connect'))
    env=tmp_path/'sources.env';env.write_text('XHS_MATERIALS_ENABLED=true\nXHS_BACKEND_TOKEN='+SECRET+'\n');env.chmod(0o600)
    assert build_material_registry(env_file=env).entries()[0].name=='xhs'
    health=source_configuration_status(env_file=env)
    x=next(s for s in health['sources'] if s['provider']=='xhs')
    assert x['configured'] and not x['live_verified'] and not x['login_live_verified']
    assert SECRET not in json.dumps(health)


@pytest.mark.parametrize('url',['http://localhost:18060','http://127.0.0.1:80','http://127.0.0.1:18060/',
    'http://127.0.0.1:18060?key=x','http://example.com:18060','https://127.0.0.1:18060',
    'http://user@127.0.0.1:18060','http://127.0.0.1:18060/a','http://127.0.0.1:018060'])
def test_endpoint_scope(url):
    with pytest.raises(SourceConfigError):XhsProfile(base_url=url,token=SECRET)


def test_references_reject_tokens_traversal_other_hosts():
    assert _parse_reference(_reference(ID))==ID
    assert _parse_reference('https://www.xiaohongshu.com/explore/'+ID)==ID
    assert _parse_reference(_reference(ID)+'#comment=c1')==ID
    for value in ['xhs://note/../'+ID,'xhs://note/%2e%2e/'+ID,'xhs://note/'+ID+'?xsec_token='+ACCESS,
                  'https://evil.example/explore/'+ID,'http://www.xiaohongshu.com/explore/'+ID,'xhs://note/'+ID+'#bad']:
        with pytest.raises(DataAdapterError):_parse_reference(value)


def test_search_read_sdk_attribution_dates_counts_and_no_credentials(tmp_path):
    p,t,c,r=setup(tmp_path)
    result=search_materials(req(xhs_comment_limit=2),registry=r)
    assert len(versions(result))==2
    for v in versions(result):
        assert v['text_scope']=='extracted_text' and v['provenance']['authority']=='ugc'
        assert v['published_on']=='2026-09-14'
        assert v['read_details']['interaction_counts']['collects']=={'raw':'2万+','exact':None}
        assert v['read_details']['comments_returned']==2 and not v['read_details']['complete']
        assert len(v['sections'])==3
    encoded=json.dumps(result.to_dict(),ensure_ascii=False)
    assert SECRET not in encoded and ACCESS not in encoded and 'private-account-name' not in encoded
    assert 'private-provider-cursor' not in encoded
    calls=[x for x in t.calls if x[1].endswith('/detail')]
    assert all(x[2]['comment_config']['max_comment_items']==2 for x in calls)
    assert all(x[2]['comment_config']['click_more_replies'] is False for x in calls)


def test_retrieve_sections_exact_offsets_and_mcp(tmp_path,monkeypatch):
    p,t,c,r=setup(tmp_path);search_materials(req(),registry=r)
    import ir_search.infrastructure.xhs_documents as module
    monkeypatch.setattr(module,'xhs_profile',lambda:p)
    monkeypatch.setattr(module,'XhsClient',lambda profile:c)
    bundle=retrieve(MaterialRequest('茅台动销',[_reference(ID)],xhs_comment_limit=3))
    assert len(bundle.materials)==1
    material=bundle.materials[0];assert material.provenance.authority.value=='ugc'
    assert material.text_provider=='xiaohongshu_mcp' and len(material.sections)==4
    assert material.read_details['comments'][2]['parent_comment_id']=='c1'
    assert material.evidence_spans
    for s in material.evidence_spans:
        assert material.text[s['start_char']:s['end_char']]==s['text']
        assert s['extra']['source_role'] in {'post','comment'}
    from ir_search.mcp_server import retrieve_payload,search_materials_payload
    assert len(retrieve_payload('茅台动销',[_reference(ID)],xhs_comment_limit=1)['materials'][0]['sections'])==2
    assert search_materials_payload(req(dry_run=True).to_dict(),registry=r)['plan']['selected_providers']==['xhs']
    assert retrieve_payload('茅台',[_reference(ID)],xhs_comment_limit=20)['status']=='error'


def test_cached_snapshot_continuation_no_repeat_and_stale_fail(tmp_path):
    p,t,c,r=setup(tmp_path)
    request=req(candidates_per_source=1,text_reads_per_source=0)
    first=search_materials(request,registry=r)
    cursor=first.coverage[0]['continuation_cursors']
    assert cursor and ACCESS not in json.dumps(first.to_dict())
    resume=replace(request,source_cursors=tuple(cursor),candidates_per_source=2)
    second=search_materials(resume,registry=r)
    assert {v['source_document_id'] for v in versions(first)}=={ID}
    assert {v['source_document_id'] for v in versions(second)}=={ID2}
    assert not second.coverage[0]['continuation_cursors']
    assert len([x for x in t.calls if x[1].endswith('/search')])==1
    c.search('茅台 动销',context=RequestContext(),refresh=True)
    stale=search_materials(resume,registry=r)
    assert 'material_cursor_stale' in {d.code for d in stale.diagnostics}
    changed=search_materials(replace(resume,xhs_sort='likes'),registry=r)
    assert 'invalid_cursor' in {d.code for d in changed.diagnostics}


def test_login_missing_prevents_search_and_cache_reads(tmp_path):
    p,t,c,r=setup(tmp_path);t.logged=False
    result=search_materials(req(),registry=r)
    assert not result.items and 'xhs_login_required' in {d.code for d in result.diagnostics}
    assert len(t.calls)==1


def test_local_dates_unknown_metadata_and_detail_failure(tmp_path):
    p,t,c,r=setup(tmp_path)
    old=detail();old['data']['note']['time']=946684800000;t.details[ID]=old
    result=search_materials(req(text_reads_per_source=1),registry=r)
    assert {v['source_document_id'] for v in versions(result)}=={ID2}
    assert versions(result)[0]['published_on'] is None
    assert versions(result)[0]['text_scope']=='metadata'
    assert 'xhs_outside_publication_window' in {d.code for d in result.diagnostics}
    t.details[ID]=DataAdapterError('web_content_challenge')
    result=search_materials(req(xhs_cache_mode='refresh'),registry=r)
    assert len(versions(result))==1 and versions(result)[0]['text_scope']=='metadata'
    assert 'web_content_challenge' in {d.code for d in result.diagnostics}
    assert result.coverage[0]['scanned_count']==1
    resumed=search_materials(req(text_reads_per_source=0,
        source_cursors=result.coverage[0]['continuation_cursors']),registry=r)
    assert {v['source_document_id'] for v in versions(resumed)}=={ID2}


def test_detail_cache_freshness_refresh_permissions_and_tamper(tmp_path):
    p,t,c,r=setup(tmp_path);ctx=RequestContext(max_operations=20)
    c.search('茅台',context=ctx)
    first=c.detail(ID,context=ctx);second=c.detail(ID,context=ctx)
    assert second.cache_state=='hit' and first.fetched_at==second.fetched_at
    assert c.detail(ID,context=ctx,refresh=True).cache_state=='fresh'
    if POSIX_PERMISSIONS:
        assert c.cache.root.stat().st_mode&0o077==0
        assert all(path.stat().st_mode&0o077==0 for path in c.cache.root.glob('*.json'))
    path=c.cache._path('detail:'+ID+':0')
    data=json.loads(path.read_text(encoding='utf-8'));data['record']['data']['note']['desc']='tampered'
    path.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(DataAdapterError,match='xhs_cache_invalid'):c.detail(ID,context=ctx)


def test_missing_access_reference_and_account_isolation(tmp_path):
    p,t,c,r=setup(tmp_path)
    with pytest.raises(DataAdapterError,match='xhs_reference_unavailable'):c.detail(ID,context=RequestContext())
    c.search('茅台',context=RequestContext())
    other=XhsClient(replace(p,token='another-synthetic-service-token'),transport=t)
    with pytest.raises(DataAdapterError,match='xhs_reference_unavailable'):other.detail(ID,context=RequestContext())
    with pytest.raises(DataAdapterError,match='entitlement_denied'):
        fetch_xhs_document(_reference(ID),context=RequestContext(account_scope='other'),client=c)


def test_body_truncation_and_comment_budget(tmp_path):
    p,t,c,r=setup(tmp_path);c.search('茅台',context=RequestContext())
    doc=fetch_xhs_document(_reference(ID),context=RequestContext(),client=c,comment_limit=3,max_chars=5)
    assert len(doc.text)==5 and len(doc.extra['sections'])==1
    assert 'text_truncated' in doc.warnings
    doc=fetch_xhs_document(_reference(ID),context=RequestContext(),client=c)
    assert len(doc.extra['sections'])==1 and not doc.extra['material_read']['comments_returned']


def test_empty_body_is_not_title_as_original(tmp_path):
    p,t,c,r=setup(tmp_path);d=detail();d['data']['note']['desc']='';t.details[ID]=d
    c.search('茅台',context=RequestContext())
    with pytest.raises(DataAdapterError,match='no_extracted_text'):
        fetch_xhs_document(_reference(ID),context=RequestContext(),client=c)


def test_large_body_truncation_is_visible(tmp_path):
    p,t,c,r=setup(tmp_path);d=detail();d['data']['note']['desc']='原'*100001;t.details[ID]=d
    c.search('茅台',context=RequestContext())
    doc=fetch_xhs_document(_reference(ID),context=RequestContext(),client=c,max_chars=100000)
    assert len(doc.text)==100000 and doc.extra['material_read']['locally_truncated']
    assert 'text_truncated' in doc.warnings


def test_wrong_note_identity_rejected(tmp_path):
    p,t,c,r=setup(tmp_path);t.details[ID]=detail(ID2);c.search('茅台',context=RequestContext())
    with pytest.raises(DataAdapterError,match='upstream_schema'):c.detail(ID,context=RequestContext())


def test_dry_run_no_network_or_private_state_writes(tmp_path):
    p,t,c,r=setup(tmp_path)
    result=search_materials(req(dry_run=True),registry=r)
    assert not t.calls and not c.cache.root.exists()
    assert result.plan['source_plans'][0]['continuation_scope']=='uninspected_snapshot_records_only'


@pytest.mark.parametrize('change',[{'xhs_sort':'invalid'},{'xhs_comment_limit':-1},{'xhs_comment_limit':True},
    {'xhs_comment_limit':20},{'xhs_cache_mode':'off'}])
def test_request_options_validated(change):
    with pytest.raises(ValueError):req(**change)
