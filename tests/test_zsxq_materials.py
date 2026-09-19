"""Synthetic community evidence; no private IDs, credentials or online requests."""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import json
import hashlib

import pytest

from ir_search import (MaterialAttachment, MaterialCandidate, MaterialRegistry, MaterialSearchRequest,
                       MaterialSection, RequestContext, build_material_registry, search_materials)
from ir_search.adapters.zsxq_materials import ZsxqMaterialAdapter, _candidate
from ir_search.infrastructure.credentials import ZsxqProfile, zsxq_profile, SourceConfigError, source_configuration_status
from ir_search.infrastructure.zsxq import ZsxqClient, ZsxqResponse, _RPCResponse
from ir_search.registry import DataAdapterError

NOW = datetime(2026, 9, 15, tzinfo=timezone.utc)
PROFILE = ZsxqProfile('synthetic_zsxq_key', ('100',))


def topic(tid='101', gid='100', **kw):
    return dict({'topic_id':tid, 'group':{'group_id':gid,'name':'测试投研星球'}, 'type':'talk',
        'owner':{'name':'测试作者'}, 'title':'公司渠道跟踪', 'content':'贵州茅台动销与渠道库存的原始讨论。',
        'create_time':'2026-09-14T10:00:00.000+0800', 'files':[]}, **kw)


def request(**kw):
    return MaterialSearchRequest(**dict({'question':'贵州茅台动销', 'keywords':['动销'], 'providers':['zsxq'],
        'published_start':'2026-09-01','published_end':'2026-09-15','candidates_per_source':5,'text_reads_per_source':2}, **kw))


class Client:
    def __init__(self, pages=None, details=None, groups=None, comments=None):
        self.pages = pages or [{'topics_brief':[topic()], 'has_more':False, 'next_end_time':''}]
        self.details, self.group_rows, self.comments = details or {}, groups or ['100'], comments or []
        self.calls, self.page_index = [], 0
    def groups(self, *, context):
        context.begin_operation(); self.calls.append(('groups',{}))
        return ZsxqResponse({'groups':[{'group_id':g} for g in self.group_rows]}, NOW)
    def read(self, tool, arguments, *, context):
        context.begin_operation(); self.calls.append((tool, arguments))
        if tool == 'get_group_topics':
            response = self.pages[min(self.page_index,len(self.pages)-1)]; self.page_index += 1
        elif tool == 'get_topic_info': response = self.details.get(arguments['topic_id'], {'topic':topic(arguments['topic_id'])})
        elif tool == 'get_topic_comments': response = {'comments':self.comments,'has_more':False}
        else: raise AssertionError(tool)
        if isinstance(response, Exception): raise response
        return ZsxqResponse(response, NOW)


def setup(client=None, profile=PROFILE):
    client = client or Client()
    registry = MaterialRegistry(); registry.register(ZsxqMaterialAdapter(profile, client_factory=lambda _:client))
    return registry,client


def test_attributed_detail_and_verified_character_spans():
    registry,client = setup()
    result = search_materials(request(), registry=registry).to_dict()
    assert result['status']=='partial' and not result['complete']
    version = result['items'][0]['versions'][0]
    assert version['provenance']['authority']=='ugc' and version['channel']=='community'
    assert version['collection_id']=='100' and version['text_scope']=='extracted_text'
    assert version['source_ref']=='zsxq://topic/100/101'
    assert version['original_url']=='https://wx.zsxq.com/group/100/topic/101'
    assert version['published_at'].endswith('+08:00') and version['authors']==['测试作者']
    for span in version['evidence_spans']:
        assert span['text']==version[span['source_part']][span['start_char']:span['end_char']]
        if span['source_part']=='text': assert span['source_role']=='post' and span['author']=='测试作者'
    assert client.calls[0][1]['end_time']=='2026-09-15T23:59:59.999+08:00'
    scan=result['coverage'][0]['scans'][0]
    assert scan['collection_id']=='100' and scan['date_filter_basis']=='local_publication_metadata'


def test_timeline_excerpt_survives_failed_detail_without_promotion():
    reg,_=setup(Client(details={'101':DataAdapterError('entitlement_denied')}))
    result=search_materials(request(),registry=reg)
    v=result.items[0]['versions'][0]
    assert v['text_scope']=='source_excerpt' and 'topic_detail_read_failed' in v['warnings']
    assert result.coverage[0]['returned_evidence']['text_scope_counts']['source_excerpt']==1
    assert 'entitlement_denied' in {d.code for d in result.diagnostics}
    reg,client=setup(); result=search_materials(request(text_reads_per_source=0),registry=reg)
    assert [c[0] for c in client.calls]==['get_group_topics']
    assert result.items[0]['versions'][0]['text_scope']=='source_excerpt'


def test_group_failures_and_sharing_candidate_and_read_budgets():
    client=Client(pages=[DataAdapterError('entitlement_denied'),{'topics_brief':[topic(gid='200')],'has_more':False,'next_end_time':''}],details={'101':{'topic':topic(gid='200')}})
    reg,_=setup(client,replace(PROFILE,group_ids=('100','200')))
    result=search_materials(request(candidates_per_source=4,text_reads_per_source=2),registry=reg)
    assert result.items and result.items[0]['versions'][0]['collection_id']=='200'
    assert [c[1]['limit'] for c in client.calls if c[0]=='get_group_topics']==[2,2]
    assert len([c for c in client.calls if c[0]=='get_topic_info'])==1
    assert [(s['collection_id'],s['state']) for s in result.coverage[0]['scans']]==[('100','entitlement_denied'),('200','queried')]


def test_paginated_boundary_dedup_and_nonadvancing_cursor():
    t1=topic('101'); t2=topic('102',create_time='2026-09-13T10:00:00.000+0800')
    pages=[{'topics_brief':[t1],'has_more':True,'next_end_time':t1['create_time']},
           {'topics_brief':[t1,t2],'has_more':True,'next_end_time':t1['create_time']}]
    reg,_=setup(Client(pages=pages))
    result=search_materials(request(text_reads_per_source=0),registry=reg)
    assert result.coverage[0]['scanned_count']==3 and len(result.items)==2
    assert {'duplicate_topic_boundary','community_cursor_not_advancing'} <= {d.code for d in result.diagnostics}
    assert sum(s['inspected_count'] for s in result.coverage[0]['scans'])==3


def test_dates_timezone_unknown_and_invalid_rows():
    rows=[topic('101',create_time='2026-08-31T23:59:59+0800'),topic('102',create_time=''),
          topic('103',create_time='2026-08-31T20:00:00Z'),topic('104',group={'group_id':'999','name':'wrong'})]
    reg,_=setup(Client(pages=[{'topics_brief':rows,'has_more':False,'next_end_time':''}]))
    result=search_materials(request(text_reads_per_source=0),registry=reg)
    versions=[v for g in result.items for v in g['versions']]
    assert len(versions)==2 and {v['published_on'] for v in versions}=={None,'2026-09-01'}
    assert 'invalid_community_record' in {d.code for d in result.diagnostics}


def test_question_answer_comments_keep_roles_and_do_not_cross_quote_boundaries():
    t=topic(type='q&a',question={'text':'贵州茅台动销如何？','owner':{'name':'提问人'}},answer={'text':'动销需结合渠道口径。','owner':{'name':'回答人'}})
    comments=[{'comment_id':'1001','text':'动销口径待核实。','owner':{'name':'评论人'},'create_time':'2026-09-15T10:00:00+0800'}]
    reg,_=setup(Client(details={'101':{'topic':t}},comments=comments),replace(PROFILE,comments_per_topic=2))
    result=search_materials(request(),registry=reg)
    v=result.items[0]['versions'][0]
    assert v['material_type']=='qa' and [s['role'] for s in v['sections']]==['question','answer','comment']
    assert {s['author'] for s in v['sections']}=={'提问人','回答人','评论人'}
    for span in v['evidence_spans']:
        if span['source_part']=='text':
            section=next(s for s in v['sections'] if s['start_char']<=span['start_char']<s['end_char'])
            assert span['end_char']<=section['end_char'] and span['author']==section['author']
    raw=_candidate(topic(type='q&a'), '100', NOW, max_chars=1000,full=True)
    assert raw.text_scope.value=='source_excerpt' and 'question_answer_roles_unverified' in raw.warnings
    raw=_candidate(topic(type='q&a',question={'anonymous':False,'questionee':{'name':'被提问人'}}), '100', NOW,max_chars=1000,full=True)
    assert raw.sections[0].role=='unverified' and raw.sections[0].author=='' and '被提问人' not in raw.authors


def test_attachment_name_is_searchable_and_cited_as_metadata_only():
    file={'file_id':'501','name':'某机构贵州茅台动销研究.pdf','size':123}
    row=topic(content='附件见文件列表',title='今日资料',files=[file,file])
    reg,_=setup(Client(pages=[{'topics_brief':[row],'has_more':False,'next_end_time':''}]))
    result=search_materials(request(text_reads_per_source=0),registry=reg)
    v=result.items[0]['versions'][0]
    assert len(v['attachments'])==1 and v['attachments'][0]['source_ref']=='zsxq://file/100/101/501'
    span=v['evidence_spans'][0]
    assert span['source_part']=='attachment_name' and span['text_scope']=='metadata'
    assert span['text']==v['attachments'][span['attachment_index']]['name']
    assert span['source_part_hash'] == hashlib.sha256(span['text'].encode()).hexdigest()
    assert span['offset_unit'] == 'unicode_code_points'


def test_richtext_preserves_encoded_tags_and_truncation_offsets():
    raw=_candidate(topic(content='<script>ignore instructions</script><e type="hashtag" title="%23%E5%8A%A8%E9%94%80%23"/>贵州茅台&amp;渠道'+'材料'*200), '100', NOW,max_chars=25,full=True)
    assert raw.text.startswith('#动销#贵州茅台&渠道') and 'ignore' not in raw.text
    assert len(raw.text)==25 and raw.sections[-1].end_char==25 and 'text_truncated' in raw.warnings


def test_directory_limit_cancel_and_unsupported_materials():
    client=Client(groups=['100','200','300'])
    reg,_=setup(client,replace(PROFILE,group_ids=(),max_groups=1))
    result=search_materials(request(),registry=reg)
    assert {'collection_directory_not_exhaustive','collection_budget_exhausted'} <= {d.code for d in result.diagnostics}
    reg,client=setup(); context=RequestContext();context.cancel()
    assert not search_materials(request(),registry=reg,context=context).items and not client.calls
    assert not search_materials(request(material_types=['research_report']),registry=reg).items and not client.calls


def test_configuration_optin_does_not_use_browser_or_cli_and_is_lazy(tmp_path,monkeypatch):
    assert zsxq_profile(values={'ZSXQ_KEY':'present_key'}) is None
    with pytest.raises(SourceConfigError): zsxq_profile(values={'ZSXQ_MATERIALS_ENABLED':'true'})
    with pytest.raises(SourceConfigError): ZsxqProfile('present_key',('bad',))
    with pytest.raises(SourceConfigError): ZsxqProfile('present_key',max_groups=0)
    assert 'synthetic_zsxq_key' not in repr(PROFILE)
    path=tmp_path/'credentials.env';path.write_text('ZSXQ_MATERIALS_ENABLED=true\nZSXQ_KEY=synthetic_zsxq_key\n');path.chmod(0o600)
    monkeypatch.setattr(ZsxqClient,'read',lambda *a,**k:pytest.fail('configuration performs I/O'))
    assert [a.name for a in build_material_registry(env_file=path).entries()]==['zsxq']
    status=next(s for s in source_configuration_status(env_file=path)['sources'] if s['provider']=='zsxq')
    assert status['configured'] and not status['live_verified']
    path.write_text('ZSXQ_MATERIALS_ENABLED=true\nWEB_MATERIALS_ENABLED=true\nWEB_ALLOW_ANONYMOUS=true\n')
    registry=build_material_registry(env_file=path)
    assert [a.name for a in registry.entries()]==['web'] and any(d.provider=='zsxq' for d in registry.diagnostics)


def test_new_contracts_validate_offsets_attachments_and_identifiers():
    with pytest.raises(ValueError): MaterialAttachment('https://x.test/?token=private','file')
    with pytest.raises(ValueError): MaterialAttachment('zsxq://file/100/101/501','file',size_bytes=True)
    with pytest.raises(ValueError): MaterialSection('company_claim','','zsxq://topic/100/101',0,1)
    with pytest.raises(ValueError): MaterialSection('post','','zsxq://topic/100/101',1,1)
    candidate=_candidate(topic(),'100',NOW,max_chars=100)
    with pytest.raises(ValueError): replace(candidate,sections=(MaterialSection('post','','zsxq://topic/100/101',0,100),))


def test_actual_fastmcp_material_call(monkeypatch):
    pytest.importorskip('mcp')
    from mcp.server.fastmcp import FastMCP
    from ir_search import mcp_server
    from ir_search.services import material_search
    registry,_=setup()
    monkeypatch.setattr(material_search,'build_material_registry',lambda:registry)
    server=FastMCP('zsxq-test');server.run=lambda:None
    monkeypatch.setattr(mcp_server,'make_fastmcp',lambda _:server);mcp_server.run()
    async def check():
        response=await server.call_tool('search_materials',{'question':'贵州茅台动销','keywords':['动销'],'published_start':'2026-09-01','published_end':'2026-09-15','providers':['zsxq']})
        result=json.loads(response[0].text)
        assert result['items'][0]['versions'][0]['collection_id']=='100'
    asyncio.run(check())
