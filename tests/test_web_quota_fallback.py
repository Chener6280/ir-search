"""Quota exhaustion is a caller handoff, not a silently substituted provider."""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import json

import pytest

from ir_search import MaterialRegistry, MaterialSearchRequest, RequestContext, search_materials, WebSearchFallback
from ir_search import mcp_server
from ir_search.adapters.web_materials import WebMaterialAdapter
from ir_search.infrastructure.credentials import WebMaterialProfile, web_material_profile, SourceConfigError
from ir_search.infrastructure.public_web import _Reply
from ir_search.infrastructure.web_search import AnySearchWebClient, BochaWebClient, ExaWebClient, WebSearchPage
from ir_search.infrastructure.web_routing import route_web_search
from ir_search.registry import DataAdapterError

NOW = datetime(2026, 9, 19, tzinfo=timezone.utc)
PROFILE = WebMaterialProfile(api_key='private-anysearch', bocha_api_key='private-bocha',
    exa_api_key='private-exa', search_provider='regional', overseas_provider='exa')


def request(**changes):
    values = dict(question='cloud revenue', keywords=['revenue'], providers=['web'],
        web_region='overseas', published_start='2025-01-01', published_end='2025-12-31',
        candidates_per_source=5, text_reads_per_source=0)
    return MaterialSearchRequest(**{**values, **changes})


class Client:
    def __init__(self, error=None):
        self.error, self.calls = error, []

    def search(self, query, *, limit, context):
        context.begin_operation()
        self.calls.append((query, limit))
        if self.error:
            raise DataAdapterError(self.error)
        return WebSearchPage([{'title':'Cloud revenue', 'url':'https://example.com/revenue',
                               'snippet':'Reported revenue'}], NOW)


def registry(bocha=None, exa=None, profile=PROFILE):
    reg = MaterialRegistry()
    unused = Client('upstream_schema')
    reg.register(WebMaterialAdapter(profile, client=unused, bocha_client=bocha or Client(),
                                   exa_client=exa or Client()))
    return reg, unused


@pytest.mark.parametrize('cls', [AnySearchWebClient, BochaWebClient, ExaWebClient])
@pytest.mark.parametrize('status,body,expected', [
    (402, {}, 'quota'),
    (403, {'message':'账户余额不足 private-exa'}, 'quota'),
    (429, {'error':'insufficient_quota'}, 'quota'),
    (429, {'error':'Rate limit: quota exceeded per minute'}, 'rate_limit'),
    (429, {}, 'rate_limit'),
    (401, {'message':'quota exhausted private-exa'}, 'authentication_failed'),
    (403, {'message':'permission denied private-exa'}, 'entitlement_denied'),
])
def test_search_error_statuses_are_distinct_and_sanitized(cls, status, body, expected):
    calls = []
    def transport(url, **kw):
        calls.append(kw)
        assert kw['search_api_errors'] is True
        return _Reply(status, 'application/json', '', json.dumps(body).encode(), NOW)
    with pytest.raises(DataAdapterError, match=expected) as caught:
        cls(PROFILE, transport=transport).search('cloud revenue', limit=2, context=RequestContext())
    assert 'private-' not in str(caught.value)
    assert len(calls) == 1


@pytest.mark.parametrize('cls,body', [
    (AnySearchWebClient, {'code':403, 'message':'insufficient balance'}),
    (BochaWebClient, {'code':403, 'msg':'账户余额不足'}),
    (ExaWebClient, {'error':'no_more_credits'}),
])
def test_quota_error_inside_http_success_is_not_an_empty_success(cls, body):
    client=cls(PROFILE, transport=lambda *a,**kw:_Reply(200,'application/json','',json.dumps(body).encode(),NOW))
    with pytest.raises(DataAdapterError, match='quota'):
        client.search('revenue',limit=1,context=RequestContext())


def test_exa_wire_contract_no_generated_summaries_and_credential_isolation():
    calls = []
    def transport(url, **kw):
        calls.append((url, kw))
        return _Reply(200, 'application/json', '', json.dumps({'results':[{
            'title':'Cloud revenue', 'url':'https://example.com/revenue', 'text':'source excerpt',
            'publishedDate':'2025-07-30T00:00:00Z', 'summary':'generated answer', 'api_key':'discard-secret'}],
            'answer':'generated answer'}).encode(), NOW)
    page = ExaWebClient(PROFILE, transport=transport).search('cloud revenue', limit=3,
        context=RequestContext(), allowed_domains=('example.com',))
    url, kw = calls[0]
    body = json.loads(kw['body'])
    assert url == 'https://api.exa.ai/search'
    assert kw['headers']['x-api-key'] == PROFILE.exa_api_key and 'Authorization' not in kw['headers']
    assert body == {'query':'cloud revenue', 'type':'auto', 'numResults':3,
                    'contents':{'text':{'maxCharacters':1000}}, 'includeDomains':['example.com']}
    assert page.rows == [{'title':'Cloud revenue', 'url':'https://example.com/revenue',
                         'snippet':'source excerpt', 'published_at':'2025-07-30T00:00:00Z'}]
    assert 'generated' not in json.dumps(page.rows)


@pytest.mark.parametrize('payload', [{'results':[1]}, {'results':None}, {'results':[],'error':'bad response'},
    {'results':[{'title':'private-exa', 'url':'https://example.com/a'}]}])
def test_exa_invalid_or_secret_echo_responses_are_rejected(payload):
    client = ExaWebClient(PROFILE, transport=lambda *a, **k:_Reply(200,'application/json','',json.dumps(payload).encode(),NOW))
    with pytest.raises(DataAdapterError, match='upstream_schema'):
        client.search('revenue',limit=1,context=RequestContext())


def test_exa_scope_and_credentials_are_checked_before_network():
    transport = lambda *a, **k:pytest.fail('unexpected call')
    with pytest.raises(ValueError): ExaWebClient(None)
    client = ExaWebClient(PROFILE, transport=transport)
    for query,limit,domains in [('',1,()),('x',True,()),('x',11,()),('x',1,('bad/domain',))]:
        with pytest.raises(DataAdapterError, match='unsupported'):
            client.search(query,limit=limit,allowed_domains=domains,context=RequestContext())
    with pytest.raises(DataAdapterError, match='no_credential'):
        ExaWebClient(replace(PROFILE,exa_api_key=''),transport=transport).search('revenue',limit=1,context=RequestContext())


def test_exa_regional_configuration_is_explicit_and_old_defaults_stay_compatible():
    p = web_material_profile(values={'WEB_MATERIALS_ENABLED':'true','WEB_SEARCH_PROVIDER':'regional',
        'WEB_OVERSEAS_PROVIDER':'exa','EXA_API_KEY':'private-exa'})
    assert route_web_search(request(),p)[0].provider == 'exa'
    assert route_web_search(request(web_region='cn'),p)[0].provider == 'bocha'
    assert p.exa_api_key not in repr(p)
    old = web_material_profile(values={'WEB_MATERIALS_ENABLED':'true','ANYSEARCH_API_KEY':'private-anysearch'})
    assert route_web_search(request(),old)[0].provider == 'anysearch'
    with pytest.raises(SourceConfigError): replace(PROFILE,overseas_provider='web_search')
    with pytest.raises(SourceConfigError): replace(PROFILE,exa_api_key='',bocha_api_key='')


def test_quota_handoff_preserves_scope_without_retry_or_fake_evidence():
    exa = Client('quota')
    p = replace(PROFILE,allowed_domains=('example.com',))
    reg, unused = registry(exa=exa, profile=p)
    out = search_materials(request(period_start='2025-04-01',period_end='2025-06-30'),registry=reg).to_dict()
    assert out['status'] == 'unavailable' and not out['items'] and not out['complete']
    assert len(exa.calls) == 1 and not unused.calls
    handoff = out['fallback_requests'][0]
    assert handoff == {'failed_provider':'exa', 'query':exa.calls[0][0], 'web_region':'overseas',
        'published_start':'2025-01-01','published_end':'2025-12-31','max_results':5,
        'allowed_domains':['example.com'], 'period_start':'2025-04-01', 'period_end':'2025-06-30',
        'reason':'quota', 'action':'caller_web_search',
        'state':'pending_caller','after_search':'retrieve','max_attempts':1,'max_text_reads':0}
    assert 'site:example.com' in handoff['query']
    assert out['request']['period_start'] == '2025-04-01'
    assert out['coverage'][0]['scans'][0]['state'] == 'quota'
    assert {d['code'] for d in out['diagnostics']} >= {'quota','caller_web_search_required'}
    assert any(g['code']=='caller_web_search_pending' and not g['completed'] for g in out['gaps'])
    assert 'private-' not in json.dumps(out)


@pytest.mark.parametrize('error', ['rate_limit','authentication_failed','entitlement_denied',
    'no_credential','network','timeout','blocked_url','upstream_schema'])
def test_non_quota_failures_do_not_request_native_search(error):
    reg, _ = registry(exa=Client(error))
    out = search_materials(request(),registry=reg)
    assert not out.fallback_requests and out.status.value=='unavailable'
    assert error in {d.code for d in out.diagnostics}


def test_cross_region_success_survives_quota_and_two_handoffs_share_budget():
    bocha,exa=Client('quota'),Client()
    reg,_=registry(bocha=bocha,exa=exa)
    out=search_materials(request(web_region='both',text_reads_per_source=0),registry=reg)
    assert out.status.value=='partial' and out.items
    assert len(out.fallback_requests)==1 and out.fallback_requests[0].failed_provider=='bocha'
    assert out.fallback_requests[0].max_results==3
    assert out.items[0]['versions'][0]['discovery_provider']=='exa'
    reg,_=registry(bocha=Client('quota'),exa=Client('quota'))
    out=search_materials(request(web_region='both',text_reads_per_source=3),registry=reg)
    assert [h.max_results for h in out.fallback_requests]==[3,2]
    assert [h.max_text_reads for h in out.fallback_requests]==[2,1]


def test_preview_empty_success_exclusion_and_budget_stop_do_not_fabricate_handoff():
    exa=Client('quota');reg,_=registry(exa=exa)
    out=search_materials(request(dry_run=True),registry=reg)
    assert not exa.calls and not out.fallback_requests
    assert out.plan['source_plans'][0]['quota_fallback']['server_executes_fallback'] is False
    out=search_materials(request(providers=[],exclude_providers=['web']),registry=reg)
    assert not out.fallback_requests and not exa.calls
    ctx=RequestContext();ctx.cancel()
    out=search_materials(request(),registry=reg,context=ctx)
    assert not out.fallback_requests and not exa.calls
    class Empty(Client):
        def search(self,*a,**k):return WebSearchPage([],NOW)
    reg,_=registry(exa=Empty())
    assert not search_materials(request(),registry=reg).fallback_requests


def test_fallback_contract_validates_scope_and_serializes_without_host_specific_api():
    handoff=WebSearchFallback('exa','revenue','overseas','2025-01-01','2025-12-31',5)
    assert handoff.to_dict()['action']=='caller_web_search'
    for changes in ({'failed_provider':'private_provider'}, {'max_results':0}, {'max_results':True},
        {'allowed_domains':('bad/url',)}, {'published_end':'2024-01-01'}, {'query':''}, {'web_region':'both'},
        {'period_start':'2025-01-01'}, {'max_text_reads':6}, {'max_text_reads':True}):
        with pytest.raises(ValueError): replace(handoff,**changes)


@pytest.mark.parametrize('tamper', ['action', 'budget', 'business_period'])
def test_fallback_cannot_expand_scope_or_override_action(tamper):
    reg,_=registry(bocha=Client('quota'),exa=Client('quota'))
    adapter=reg.entries()[0];original=adapter.search_materials
    def altered(req,**kw):
        page=original(req,**kw)
        if tamper=='action':
            object.__setattr__(page.fallback_requests[0], 'action', 'run_arbitrary_tool')
        elif tamper=='budget':
            page.fallback_requests[0]=replace(page.fallback_requests[0],max_results=5)
        else:
            page.fallback_requests[0]=replace(page.fallback_requests[0],period_start='2025-01-01',period_end='2025-12-31')
        return page
    adapter.search_materials=altered
    out=search_materials(request(web_region='both'),registry=reg)
    assert not out.fallback_requests and 'invalid_material_response' in {d.code for d in out.diagnostics}


def test_fallback_cannot_be_attached_to_a_successful_source_scan():
    reg,_=registry()
    adapter=reg.entries()[0]; original=adapter.search_materials
    def altered(req,**kw):
        page=original(req,**kw)
        page.fallback_requests.append(WebSearchFallback('exa','revenue','overseas',req.published_start,req.published_end,2))
        return page
    adapter.search_materials=altered
    out=search_materials(request(),registry=reg)
    assert not out.fallback_requests and 'invalid_material_response' in {d.code for d in out.diagnostics}


def test_mcp_payload_exposes_pending_handoff_and_caller_instructions():
    reg,_=registry(exa=Client('quota'))
    out=mcp_server.search_materials_payload(request().to_dict(),registry=reg)
    assert out['fallback_requests'][0]['state']=='pending_caller'
    json.dumps(out,allow_nan=False)
    assert 'native web search' in mcp_server.MCP_INSTRUCTIONS
    assert 'fallback_requests' in mcp_server.TOOL_DESCRIPTIONS['search_materials']


def test_real_mcp_runtime_returns_handoff(monkeypatch):
    runtime=pytest.importorskip('mcp.server.fastmcp')
    from ir_search.services import material_search
    reg,_=registry(exa=Client('quota'))
    monkeypatch.setattr(material_search,'build_material_registry',lambda:reg)
    server=runtime.FastMCP('quota');server.run=lambda:None
    monkeypatch.setattr(mcp_server,'make_fastmcp',lambda _:server)
    mcp_server.run()
    async def check():
        response=await server.call_tool('search_materials',request().to_dict())
        out=json.loads(response[0].text)
        assert out['fallback_requests'][0]['failed_provider']=='exa'
        assert out['status']=='unavailable'
    asyncio.run(check())
