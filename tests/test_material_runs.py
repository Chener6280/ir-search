"""Private operational records and resume helpers use synthetic sources only."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from _platform import POSIX_PERMISSIONS, symlinks_supported
from ir_search import record_material_run, next_material_request, search_materials, RequestContext
from ir_search.contracts import Diagnostic
from ir_search.context import RequestStopped
from ir_search.infrastructure.material_cursor import _decode, _continuation
from ir_search.registry import DataAdapterError
from test_xhs_materials import setup, req, versions, ID, ID2


def test_audit_opt_in_private_immutable_and_positive_redaction(tmp_path):
    p,t,c,reg=setup(tmp_path)
    r=search_materials(req(candidates_per_source=1,text_reads_per_source=0),registry=reg)
    assert r.audit=={}
    r.diagnostics.append(Diagnostic('synthetic_private_secret','search_materials',message='private-user-message'))
    r.request_id='private-account-identifying-request'
    output=tmp_path/'runs'
    a=record_material_run(r,output);raw=Path(a['path']).read_text(encoding='utf-8');stored=json.loads(raw)
    assert a['status']=='recorded' and stored['summary']['source_text_included'] is False
    for secret in ['synthetic_private_secret','private-user-message','private-account-identifying-request',
                   r.request.question,ID,'xhs://','合成测试作者']:
        assert secret not in raw
    assert 'unknown_diagnostic' in raw
    assert record_material_run(r,output)['status']=='reused'
    if POSIX_PERMISSIONS:
        assert output.stat().st_mode&0o077==0 and Path(a['path']).stat().st_mode&0o077==0
    r.items[0]['versions'][0]['title']='changed source version'
    assert record_material_run(r,output)['run_id']!=a['run_id']


def test_record_tamper_or_unsafe_destination_never_overwrites(tmp_path):
    p,t,c,reg=setup(tmp_path);r=search_materials(req(),registry=reg)
    a=record_material_run(r,tmp_path/'runs');path=Path(a['path']);path.write_text('{}',encoding='utf-8')
    assert record_material_run(r,tmp_path/'runs')['status']=='error' and path.read_text(encoding='utf-8')=='{}'
    if symlinks_supported():
        link=tmp_path/'link';link.symlink_to(tmp_path/'runs',target_is_directory=True)
        assert record_material_run(r,link)['code']=='run_record_unavailable'
    if POSIX_PERMISSIONS:
        unsafe=tmp_path/'unsafe';unsafe.mkdir();unsafe.chmod(0o755)
        assert record_material_run(r,unsafe)['status']=='error'
    ctx=RequestContext();ctx.cancel()
    assert record_material_run(r,tmp_path/'cancelled',context=ctx)['code']=='cancelled'


def test_search_and_mcp_recording_does_not_lose_results_on_storage_failure(tmp_path, monkeypatch):
    monkeypatch.setenv('IR_SEARCH_OUTPUT_ROOT', str(tmp_path))  # MCP writes are confined to one root
    p,t,c,reg=setup(tmp_path)
    file=tmp_path/'file';file.write_text('not a directory')
    r=search_materials(req(),registry=reg,audit_dir=file)
    assert r.items and r.audit['status']=='error'
    from ir_search.mcp_server import search_materials_payload
    payload=search_materials_payload(req().to_dict(),registry=reg,audit_dir=str(tmp_path/'runs'))
    assert payload['items'] and payload['audit']['status']=='recorded'
    with pytest.raises(ValueError):search_materials(req(),registry=reg,audit_dir='')


def test_resume_only_sources_with_real_cursors_and_budgets_may_change(tmp_path):
    p,t,c,reg=setup(tmp_path)
    first=search_materials(req(candidates_per_source=1,text_reads_per_source=0),registry=reg)
    first.coverage.append({'provider':'web','state':'queried','continuation_cursors':[]})
    following=next_material_request(first,candidates_per_source=2,text_reads_per_source=1)
    assert following.providers==('xhs',) and following.text_reads_per_source==1
    second=search_materials(following,registry=reg)
    assert {v['source_document_id'] for v in versions(second)}=={ID2}
    assert next_material_request(second) is None
    with pytest.raises(DataAdapterError,match='invalid_cursor'):
        next_material_request(first,context=RequestContext(account_scope='other'))
    with pytest.raises(ValueError):next_material_request(first,candidates_per_source=500)
    first.gaps.append({'code':'result_limit_reached'})
    with pytest.raises(ValueError):next_material_request(first)


def test_no_resume_of_preview_or_cursor_from_wrong_source(tmp_path):
    p,t,c,reg=setup(tmp_path)
    with pytest.raises(ValueError):next_material_request(search_materials(req(dry_run=True),registry=reg))
    r=search_materials(req(candidates_per_source=1,text_reads_per_source=0),registry=reg)
    r.coverage[0]['provider']='ima'
    with pytest.raises(DataAdapterError):next_material_request(r)


def test_long_valid_cursor_uses_own_bound_not_query_bound():
    from ir_search import MaterialSearchRequest
    r=MaterialSearchRequest('观测'*900,providers=['ima'],published_start='2026-09-01',published_end='2026-09-18')
    token=_continuation(r,'ima','kb',r.question,'',[{},{}],1,'',None,RequestContext())
    assert 2000 < len(token) <= 12000
    assert replace(r,source_cursors=[token]).source_cursors==(token,)
    assert _decode(token)['query']==r.question


@pytest.mark.parametrize('code',['rate_limit','authentication_failed','web_content_challenge','timeout'])
def test_wechat_stops_account_requests_and_preserves_prior_page_cursor(code):
    from test_wechat_materials import setup,request,row,PROFILE,ACCOUNT,NOW,WechatHistoryPage
    first=WechatHistoryPage((row(),),ACCOUNT.name,ACCOUNT.ghid,'next-page',True,NOW)
    reg,c,_=setup(profile=replace(PROFILE,max_pages_per_account=2),pages=[first,DataAdapterError(code)])
    r=search_materials(request(candidates_per_source=5,text_reads_per_source=0),registry=reg)
    assert len(c.calls)==2 and r.items
    assert _decode(r.coverage[0]['continuation_cursors'][0])['cursor']=='next-page'
    assert next_material_request(r).providers==('wechat',)


def test_ima_interruption_preserves_cursor_and_stops_other_queries():
    from test_ima_materials import setup,Client,req,PROFILE
    calls=[]
    def page(params):
        calls.append(params)
        if len(calls)==2:raise RequestStopped('operation_budget_exhausted')
        return {'info_list':[{'media_id':'m1','title':'茅台动销','highlight_content':'动销'}], 'is_end':False,'next_cursor':'next-page'}
    reg,c=setup(Client({'search_knowledge':page}),profile=replace(PROFILE,max_pages=2))
    r=search_materials(req(ima_include_notes=False,text_reads_per_source=0,keywords=['动销','其他']),registry=reg)
    assert r.items and len(calls)==2
    assert _decode(r.coverage[0]['continuation_cursors'][0])['cursor']=='next-page'


def test_zsxq_limit_during_detail_retains_uninspected_candidates():
    from test_zsxq_materials import Client,setup,request,topic
    class Limited(Client):
        def read(self,operation,params,*,context):
            if operation=='get_topic_info':raise DataAdapterError('rate_limit')
            return super().read(operation,params,context=context)
    reg,c=setup(Limited(pages=[{'topics_brief':[topic('101'),topic('102')], 'has_more':False,'next_end_time':''}]))
    r=search_materials(request(candidates_per_source=2,text_reads_per_source=2),registry=reg)
    assert r.coverage[0]['scanned_count']==1 and len(r.items)==1
    resume=next_material_request(r,text_reads_per_source=0)
    next_result=search_materials(resume,registry=reg)
    assert {v['source_document_id'] for i in next_result.items for v in i['versions']}=={'102'}
