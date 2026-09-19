"""Regional web routing, credential separation and bounded multi-engine evidence."""
from dataclasses import replace
from datetime import datetime, timezone
import asyncio
import json

import pytest

from ir_search import MaterialRegistry, MaterialSearchRequest, RequestContext, search_materials
from ir_search.adapters.web_materials import WebMaterialAdapter
from ir_search.infrastructure.credentials import WebMaterialProfile, web_material_profile, SourceConfigError
from ir_search.infrastructure.public_web import _Reply
from ir_search.infrastructure.web_routing import route_web_search
from ir_search.infrastructure.web_search import BochaWebClient, AnySearchWebClient, WebSearchPage
from ir_search.registry import DataAdapterError

NOW=datetime(2026,9,16,tzinfo=timezone.utc)
PROFILE=WebMaterialProfile('synthetic_anysearch',search_provider='regional',bocha_api_key='synthetic_bocha')


def request(question='中国人工智能',**kw):
    return MaterialSearchRequest(question,**dict({'published_start':'2026-09-01','published_end':'2026-09-16',
        'providers':['web'],'keywords':['人工智能'],'text_reads_per_source':0},**kw))


@pytest.mark.parametrize('question,kw,providers,basis',[
    ('美国人工智能',{},['anysearch'],'market_entity_rules'),
    ('英伟达产业链',{},['anysearch'],'market_entity_rules'),
    ('China artificial intelligence',{},['bocha'],'market_entity_rules'),
    ('跨市场人工智能',{'symbols':['600519.SH','NVDA']},['bocha','anysearch'],'market_entity_rules'),
    ('腾讯',{'symbols':['00700.HK']},['anysearch'],'market_entity_rules'),
    ('渠道库存',{},['bocha'],'language_default_unverified'),
    ('Industrial demand',{'keywords':['demand']},['anysearch'],'language_default_unverified'),
    ('国内消费',{'web_region':'overseas'},['anysearch'],'explicit_request'),
    ('美国消费',{'web_region':'cn'},['bocha'],'explicit_request'),
    ('需求',{'web_region':'both'},['bocha','anysearch'],'explicit_request'),
])
def test_deterministic_routes_prefer_explicit_scope_and_disclose_heuristics(question,kw,providers,basis):
    routes=route_web_search(request(question,**kw),PROFILE)
    assert [r.provider for r in routes]==providers and {r.basis for r in routes}=={basis}


def test_fixed_provider_compatibility_and_invalid_scope():
    route=route_web_search(request(),replace(PROFILE,search_provider='anysearch'))[0]
    assert route.provider=='anysearch' and route.basis=='configured_provider'
    for value in ['US',False,[],None]:
        with pytest.raises(ValueError):request(web_region=value)
    with pytest.raises(ValueError):route_web_search({},PROFILE)
    with pytest.raises(ValueError):route_web_search(request(),None)


def test_configuration_supports_partial_keys_without_cross_provider_fallback():
    p=web_material_profile(values={'WEB_MATERIALS_ENABLED':'true','WEB_SEARCH_PROVIDER':'regional','BOCHA_API_KEY':'synthetic_bocha'})
    assert p.search_provider=='regional' and not p.api_key
    with pytest.raises(DataAdapterError,match='no_credential'):
        AnySearchWebClient(p,transport=lambda *a,**k:pytest.fail('credential fallback')).search('test',limit=1,context=RequestContext())
    p=replace(PROFILE,bocha_api_key='')
    with pytest.raises(DataAdapterError,match='no_credential'):
        BochaWebClient(p,transport=lambda *a,**k:pytest.fail('credential fallback')).search('test',limit=1,context=RequestContext())
    with pytest.raises(SourceConfigError):WebMaterialProfile(search_provider='regional')
    with pytest.raises(SourceConfigError):replace(PROFILE,search_provider='unknown')
    assert PROFILE.api_key not in repr(PROFILE) and PROFILE.bocha_api_key not in repr(PROFILE)


def bocha_payload(**changes):
    return {'code':200,'data':{'webPages':{'value':[dict({'name':'中国人工智能','url':'https://example.gov.cn/a',
        'snippet':'人工智能政策摘要','summary':'discard summary','content':'discard content',
        'dateLastCrawled':'2026-09-16','siteName':'unverified identity'},**changes)]}}}


def test_bocha_native_domain_filter_and_input_validation():
    calls = []
    def transport(url, **kw):
        calls.append(json.loads(kw['body']))
        return _Reply(200, 'application/json', '', json.dumps(bocha_payload()).encode(), NOW)
    client = BochaWebClient(PROFILE, transport=transport)
    client.search('政策', limit=2, context=RequestContext(), allowed_domains=('csrc.gov.cn', 'pbc.gov.cn'))
    assert calls[0]['include'] == 'csrc.gov.cn|pbc.gov.cn'
    for domains in (['csrc.gov.cn'], ('csrc.gov.cn|evil.test',), ('https://csrc.gov.cn',), ('a..cn',), (None,)):
        with pytest.raises(DataAdapterError, match='unsupported'):
            client.search('政策', limit=2, context=RequestContext(), allowed_domains=domains)
    assert len(calls) == 1


def test_bocha_wire_fields_key_isolation_and_publication_not_crawl_time():
    calls=[]
    def transport(url,**kw):
        calls.append((url,kw))
        return _Reply(200,'application/json','',json.dumps(bocha_payload()).encode(),NOW)
    client=BochaWebClient(PROFILE,transport=transport)
    page=client.search('中国人工智能',limit=2,context=RequestContext())
    url,kw=calls[0]
    assert url=='https://api.bochaai.com/v1/web-search'
    assert kw['headers']['Authorization']=='Bearer '+PROFILE.bocha_api_key
    assert PROFILE.api_key not in json.dumps(kw['headers'])
    assert json.loads(kw['body'])=={'query':'中国人工智能','count':2,'freshness':'noLimit','summary':False}
    assert set(page.rows[0])=={'title','url','snippet'}
    assert 'discard' not in repr(page.rows)
    for query,limit in [('',1),('a',True),('a',11)]:
        with pytest.raises(DataAdapterError):client.search(query,limit=limit,context=RequestContext())
    assert len(calls)==1
    with pytest.raises(ValueError):BochaWebClient(None)


@pytest.mark.parametrize('payload,code',[
    ({'code':401,'message':'synthetic_bocha'},'authentication_failed'),
    ({'code':403},'entitlement_denied'),({'code':429},'rate_limit'),({'code':500},'network'),
    ({'code':True},'upstream_schema'),({'code':200,'data':{}},'upstream_schema'),
    ({'code':200,'data':{'webPages':{'value':[1]}}},'upstream_schema'),
    (bocha_payload(snippet='synthetic_bocha'),'upstream_schema'),
])
def test_bocha_failures_remain_sanitized(payload,code):
    client=BochaWebClient(PROFILE,transport=lambda *a,**k:_Reply(200,'application/json','',json.dumps(payload).encode(),NOW))
    with pytest.raises(DataAdapterError,match=code) as exc:client.search('query',limit=2,context=RequestContext())
    assert 'synthetic_' not in str(exc.value)


@pytest.mark.parametrize('reply',[_Reply(302,'','https://untrusted.test',b'',NOW),_Reply(200,'text/html','',b'error',NOW),_Reply(200,'application/json','',b'NaN',NOW)])
def test_bocha_redirects_or_invalid_protocol_are_not_followed(reply):
    calls=[]
    def transport(*a,**k):calls.append(a);return reply
    with pytest.raises(DataAdapterError):BochaWebClient(PROFILE,transport=transport).search('query',limit=1,context=RequestContext())
    assert len(calls)==1


class Client:
    def __init__(self,host,error=None):self.host,self.error,self.calls=host,error,[]
    def search(self,query,*,limit,context):
        context.begin_operation();self.calls.append((query,limit))
        if self.error:raise DataAdapterError(self.error)
        return WebSearchPage([{'title':'人工智能','url':f'https://{self.host}/{i}', 'snippet':'人工智能 demand'} for i in range(limit)],NOW)


def setup(bocha_error=None,any_error=None,reader=None):
    bocha,anysearch=Client('example.gov.cn',bocha_error),Client('example.com',any_error)
    registry=MaterialRegistry()
    registry.register(WebMaterialAdapter(PROFILE,client=anysearch,bocha_client=bocha,reader=reader))
    return registry,bocha,anysearch


def test_both_regions_share_candidate_and_read_budgets():
    reads=[]
    def reader(url,**kw):reads.append(url);raise DataAdapterError('entitlement_denied')
    registry,bocha,anysearch=setup(reader=reader)
    result=search_materials(request(web_region='both',candidates_per_source=5,text_reads_per_source=3),registry=registry).to_dict()
    assert [bocha.calls[0][1],anysearch.calls[0][1]]==[3,2] and len(reads)==3
    assert len(result['items'])==5 and result['coverage'][0]['scanned_count']==5
    assert {s['discovery_provider'] for s in result['coverage'][0]['scans']}=={'bocha','anysearch'}
    versions=[v for g in result['items'] for v in g['versions']]
    assert {v['discovery_provider'] for v in versions}=={'bocha','anysearch'}
    assert all(v['text_scope']=='search_snippet' for v in versions)


def test_one_candidate_budget_reports_unscanned_region():
    registry,bocha,anysearch=setup()
    result=search_materials(request(web_region='both',candidates_per_source=1),registry=registry).to_dict()
    assert bocha.calls and not anysearch.calls and result['items']
    scan=result['coverage'][0]['scans'][1]
    assert scan['state']=='web_region_budget_exhausted' and scan['web_region']=='overseas'


def test_engine_failure_preserves_success_and_all_failures_are_unavailable():
    registry,bocha,anysearch=setup(bocha_error='authentication_failed')
    result=search_materials(request(web_region='both',candidates_per_source=4),registry=registry).to_dict()
    assert result['status']=='partial' and len(result['items'])==2
    assert result['coverage'][0]['scans'][0]['state']=='authentication_failed'
    registry,bocha,anysearch=setup('authentication_failed','quota')
    result=search_materials(request(web_region='both'),registry=registry).to_dict()
    assert result['status']=='unavailable' and not result['items']
    assert result['coverage'][0]['state']=='source_queries_failed' and len(result['coverage'][0]['scans'])==2
    assert 'no_match_in_scanned_records' not in {gap['code'] for gap in result['gaps']}


def test_same_url_from_two_engines_retains_both_discovery_versions():
    registry,bocha,anysearch=setup()
    anysearch.host=bocha.host
    result=search_materials(request(web_region='both',candidates_per_source=2),registry=registry).to_dict()
    assert len(result['items'])==1 and len(result['items'][0]['versions'])==2
    assert {v['discovery_provider'] for v in result['items'][0]['versions']}=={'bocha','anysearch'}
    assert result['items'][0]['independence']=='not_established'


def test_selected_region_does_not_call_other_engine():
    registry,bocha,anysearch=setup()
    result=search_materials(request(web_region='cn'),registry=registry).to_dict()
    assert bocha.calls and not anysearch.calls
    assert all(v['discovery_provider']=='bocha' for g in result['items'] for v in g['versions'])


def test_real_fastmcp_region_parameter(monkeypatch):
    pytest.importorskip('mcp')
    from mcp.server.fastmcp import FastMCP
    from ir_search import mcp_server
    from ir_search.services import material_search
    registry,bocha,anysearch=setup()
    monkeypatch.setattr(material_search,'build_material_registry',lambda:registry)
    server=FastMCP('regional-web');server.run=lambda:None
    monkeypatch.setattr(mcp_server,'make_fastmcp',lambda _:server);mcp_server.run()
    async def check():
        response=await server.call_tool('search_materials',request(web_region='overseas').to_dict())
        result=json.loads(response[0].text)
        assert result['request']['web_region']=='overseas' and anysearch.calls and not bocha.calls
        assert result['coverage'][0]['scans'][0]['discovery_provider']=='anysearch'
    asyncio.run(check())
