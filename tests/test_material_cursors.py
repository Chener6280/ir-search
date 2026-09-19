"""Continuation is a bounded scan position, never a promise of full coverage."""
from dataclasses import replace
import base64
import json

import pytest

from ir_search import MaterialSearchRequest, RequestContext, search_materials
from ir_search.infrastructure.material_cursor import _continuation, _decode, _states, _slice
from ir_search.registry import DataAdapterError


def request(**changes):
    return MaterialSearchRequest(**dict(dict(question='投资', providers=['ima'],
        published_start='2026-09-01', published_end='2026-09-17'), **changes))


def token(req=None, **changes):
    return _continuation(req or request(), 'ima', 'kb1', '投资', '', [{'id':1},{'id':2}],
                         1, '', None, RequestContext(), **changes)


def test_query_date_account_bound_but_budgets_can_change():
    req=request(source_cursors=[token()])
    assert _states(replace(req,candidates_per_source=2), 'ima', RequestContext())
    for changed, context in ((replace(req,question='别的问题'), RequestContext()),
                             (replace(req,published_start='2026-09-02'), RequestContext()),
                             (req, RequestContext(account_scope='another'))):
        with pytest.raises(DataAdapterError,match='invalid_cursor'): _states(changed,'ima',context)


@pytest.mark.parametrize('value',['', 'not-base64!', 'a'*12001, base64.urlsafe_b64encode(b'{}').decode()])
def test_malformed_cursor_rejected(value):
    with pytest.raises((ValueError,DataAdapterError)): request(source_cursors=[value])


def test_provider_explicit_and_duplicates_rejected():
    with pytest.raises(ValueError): request(providers=[],source_cursors=[token()])
    t=token()
    # Contract may deduplicate identical strings, but conflicting positions cannot coexist.
    state=_decode(t);state['cursor']='another'
    other=base64.urlsafe_b64encode(json.dumps(state).encode()).decode().rstrip('=')
    with pytest.raises(DataAdapterError): _states(request(source_cursors=[t,other]),'ima',RequestContext())


def test_page_change_never_silently_advances():
    state=_decode(token())
    assert _slice([{'id':1},{'id':2}],3,state)==([{'id':2}],2)
    with pytest.raises(DataAdapterError,match='material_cursor_stale'):
        _slice([{'id':9},{'id':2}],3,state)


def test_end_flag_wins_and_no_guessed_next_page():
    for more in (False,None):
        assert _continuation(request(),'ima','kb1','投资','',[{}],1,'stray',more,RequestContext()) is None


def test_ima_sdk_resume_uninspected_unknown_page_without_repeating():
    from test_ima_materials import Client, setup, req
    rows=[{'media_id':f'm{i}','title':f'茅台动销第{i}份','highlight_content':'动销'} for i in range(5)]
    reg,c=setup(Client({'search_knowledge':{'info_list':rows}}))
    r=req(candidates_per_source=2,text_reads_per_source=0,ima_include_notes=False)
    first=search_materials(r,registry=reg)
    second=search_materials(replace(r,candidates_per_source=5,source_cursors=tuple(first.coverage[0]['continuation_cursors'])),registry=reg)
    refs=lambda result:{v['source_ref'] for i in result.items for v in i['versions']}
    assert len(refs(first))==2 and len(refs(second))==3 and refs(first).isdisjoint(refs(second))
    assert not second.coverage[0]['continuation_cursors']
    assert 'ima_pagination_unknown' in {d.code for d in second.diagnostics}


def test_wechat_resume_clipped_page_and_stop_on_changed_snapshot():
    from test_wechat_materials import setup,request,row
    reg,c,_=setup([row(i) for i in range(100,105)])
    req=request(candidates_per_source=2,text_reads_per_source=0)
    first=search_materials(req,registry=reg)
    resume=replace(req,candidates_per_source=4,source_cursors=tuple(first.coverage[0]['continuation_cursors']))
    second=search_materials(resume,registry=reg)
    assert len(first.items)==2 and len(second.items)==3
    assert not second.coverage[0]['continuation_cursors']
    c.rows.insert(0,row(99))
    stale=search_materials(resume,registry=reg)
    assert not stale.items and 'material_cursor_stale' in {d.code for d in stale.diagnostics}


def test_zsxq_next_page_carries_boundary_dedup_and_explicit_scope():
    from test_zsxq_materials import Client,setup,request,topic
    firstrow=topic('101');secondrow=topic('102',create_time='2026-09-13T10:00:00+0800')
    reg,c=setup(Client(pages=[{'topics_brief':[firstrow],'has_more':True,'next_end_time':firstrow['create_time']},
        {'topics_brief':[firstrow,secondrow],'has_more':False,'next_end_time':''}]))
    req=request(candidates_per_source=1,text_reads_per_source=0,zsxq_group_ids=['100'])
    first=search_materials(req,registry=reg)
    second=search_materials(replace(req,candidates_per_source=3,source_cursors=tuple(first.coverage[0]['continuation_cursors'])),registry=reg)
    assert len(second.items)==1 and second.items[0]['versions'][0]['source_document_id']=='102'
    assert 'duplicate_topic_boundary' in {d.code for d in second.diagnostics}
    denied=search_materials(replace(req,zsxq_group_ids=('999',)),registry=reg)
    assert 'entitlement_denied' in {d.code for d in denied.diagnostics}


def test_zsxq_clipped_page_reuses_original_page_size_after_budget_change():
    from test_zsxq_materials import Client,setup,request,topic
    rows=[topic(str(i),title=f'动销{i}') for i in range(101,105)]
    reg,c=setup(Client(pages=[{'topics_brief':rows,'has_more':False,'next_end_time':''}]))
    req=request(candidates_per_source=2,text_reads_per_source=0)
    first=search_materials(req,registry=reg)
    second=search_materials(replace(req,candidates_per_source=5,source_cursors=tuple(first.coverage[0]['continuation_cursors'])),registry=reg)
    assert c.calls[0][1]['limit']==c.calls[1][1]['limit']==2
    assert {v['source_document_id'] for i in second.items for v in i['versions']}=={'103','104'}


def test_nonadvancing_and_out_of_window_zsxq_cursors_not_offered():
    from test_zsxq_materials import Client,setup,request,topic
    for cursor in ('2026-09-16T00:00:00+0800','2026-08-01T00:00:00+0800'):
        reg,_=setup(Client(pages=[{'topics_brief':[topic()], 'has_more':True,'next_end_time':cursor}]))
        r=search_materials(request(candidates_per_source=1,text_reads_per_source=0),registry=reg)
        assert not r.coverage[0]['continuation_cursors']
