"""Deterministic engine adapters, ordered failover, isolated auth and wheel resources."""
from dataclasses import replace
from datetime import datetime, timezone
import importlib
import json

import pytest

from ir_search import MaterialRegistry, RequestContext, search_materials, WebSearchFallback
from ir_search.adapters.web_materials import WebMaterialAdapter
from ir_search.infrastructure.credentials import WebMaterialProfile, web_material_profile, SourceConfigError, WEB_ENGINES
from ir_search.infrastructure.public_web import _Reply
from ir_search.infrastructure.web_search import WebSearchPage, TavilyWebClient, ExaWebClient
from ir_search.registry import DataAdapterError
from tests.test_web_quota_fallback import request

NOW = datetime(2026, 10, 24, tzinfo=timezone.utc)
PROFILE = WebMaterialProfile(search_provider='chain', **{name + '_api_key': 'private-' + name for name in
    ('baizhi','zai','brave','serper','perplexity','tavily','bocha','exa')}, api_key='private-anysearch')
NEW = ('baizhi','zai','brave','serper','perplexity','duckduckgo','tavily')
ROW = {'title':'NVIDIA earnings', 'url':'https://example.com/earnings', 'snippet':'NVIDIA earnings source text'}


def adapter(name, transport):
    cls = TavilyWebClient if name == 'tavily' else getattr(importlib.import_module('ir_search.adapters.' + name),
        {'baizhi':'BaizhiAdapter','zai':'ZaiAdapter','brave':'BraveAdapter','serper':'SerperAdapter',
         'perplexity':'PerplexityAdapter','duckduckgo':'DuckDuckGoAdapter'}[name])
    return cls(PROFILE, transport=transport)


def transport_for(name, state, calls):
    def transport(url, **kw):
        calls.append((url, kw))
        body = json.loads(kw['body']) if kw['body'] else {}
        if name in {'baizhi', 'zai'}:
            if kw['method'] == 'DELETE': return _Reply(204,'','',b'',NOW)
            if body.get('method') == 'initialize':
                return _Reply(200,'application/json','',json.dumps({'jsonrpc':'2.0','id':1,
                    'result':{'protocolVersion':'2024-11-05'}}).encode(),NOW,{'Mcp-Session-Id':'safe-session'})
            if body.get('method') == 'notifications/initialized': return _Reply(202,'','',b'',NOW)
        if state == 'timeout': raise TimeoutError('raw private-secret')
        if state in {'quota','rate_limit','auth','server'}:
            status = {'quota':402,'rate_limit':429,'auth':401,'server':503}[state]
            return _Reply(status,'application/json','',b'{"message":"private-upstream-generated-credential"}',NOW)
        if state == 'malformed': return _Reply(200,'application/json','',b'{"wrong":"shape"}',NOW)
        rows = [] if state == 'empty' else [ROW]
        value = {'results':rows}
        if name == 'brave': value = {'web':{'results':[{**r,'description':r['snippet']} for r in rows]}}
        if name == 'serper': value = {'organic':[{**r,'link':r['url']} for r in rows]}
        if name == 'tavily': value = {'results':[{**r,'content':r['snippet']} for r in rows]}
        if name == 'duckduckgo':
            html = '<div class="no-results__message">none</div>' if not rows else '<div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fearnings">NVIDIA <b>earnings</b></a><a class="result__snippet">NVIDIA earnings source text</a></div>'
            return _Reply(200,'text/html','',html.encode(),NOW)
        if name in {'baizhi','zai'}:
            value = rows if name == 'baizhi' else [{**r,'link':r['url'],'content':r['snippet'],'published_at':'2026-01-01'} for r in rows]
            value = {'jsonrpc':'2.0','id':2,'result':{'content':[{'type':'text','text':json.dumps(value)}]}}
        return _Reply(200,'application/json','',json.dumps(value).encode(),NOW)
    return transport


@pytest.mark.parametrize('name', NEW)
@pytest.mark.parametrize('state', ('ok','empty','quota','rate_limit','auth','timeout','malformed','server'))
def test_engine_http_matrix(name, state):
    calls = []
    engine = adapter(name, transport_for(name,state,calls))
    if state in {'ok','empty'}:
        page = engine.search('NVIDIA earnings',limit=3,context=RequestContext())
        assert len(page.rows) == (state == 'ok')
        if name == 'zai' and page.rows: assert 'published_at' not in page.rows[0]
        serialized = json.dumps(page.rows)
        assert 'private-' not in serialized
    else:
        expected={'quota':'quota','rate_limit':'rate_limit','auth':'authentication_failed',
                  'timeout':'timeout','malformed':'upstream_schema','server':'network'}[state]
        with pytest.raises(DataAdapterError) as caught:
            engine.search('NVIDIA earnings',limit=3,context=RequestContext())
        assert str(caught.value) == expected
        assert 'private' not in str(caught.value)
    for url, kw in calls:
        assert url.startswith('https://') and 'private-' not in url
        headers = kw['headers']
        if name == 'duckduckgo': assert not any('key' in k.lower() or 'authorization' == k.lower() for k in headers)
        elif name == 'brave': assert kw['method']=='GET' and headers['X-Subscription-Token']=='private-brave'
        elif name == 'serper': assert headers['X-API-KEY']=='private-serper'
        else: assert headers['Authorization']=='Bearer private-' + name
        if name == 'perplexity': assert url=='https://api.perplexity.ai/search'
    if name in {'baizhi','zai'}:
        call = json.loads(calls[2][1]['body'])
        assert call['params']['name'] == ('websearch_search' if name=='baizhi' else 'web_search_prime')
        assert call['params']['arguments']['count'] == 3
        assert calls[-1][1]['method'] == 'DELETE'


class Client:
    def __init__(self, name, outcomes, calls): self.name,self.outcomes,self.calls=name,outcomes,calls
    def search(self, *args, **kwargs):
        self.calls.append(self.name)
        result=self.outcomes.get(self.name, 'ok')
        if result not in {'ok','empty'}: raise DataAdapterError(result)
        return WebSearchPage([ROW] if result=='ok' else [],NOW)


def run_chain(outcomes, *, profile=PROFILE, region='cn', **options):
    calls=[]
    reg=MaterialRegistry()
    reg.register(WebMaterialAdapter(profile, clients={name:Client(name,outcomes,calls) for name in WEB_ENGINES}))
    out=search_materials(request(question='NVIDIA earnings',keywords=['earnings'],web_region=region,**options),registry=reg).to_dict()
    return out,calls


def test_order_skips_credentials_and_empty_is_success():
    p=replace(PROFILE,baizhi_api_key='')
    out,calls=run_chain({'zai':'quota','bocha':'empty'},profile=p)
    assert calls==['zai','bocha'] and not out['fallback_requests']
    scans=out['coverage'][0]['scans']
    assert [s['state'] for s in scans]==['no_credentials','quota','queried']
    assert all(s['elapsed_ms']>=0 and s['web_region']=='cn' for s in scans)


@pytest.mark.parametrize('reason', ('quota','rate_limit','network','timeout','authentication_failed','entitlement_denied','upstream_schema','tls_error'))
def test_failure_categories_advance_once(reason):
    out,calls=run_chain({'baizhi':reason})
    assert calls==['baizhi','zai'] and not out['fallback_requests']
    assert out['coverage'][0]['scans'][0]['state']==reason


def test_whole_chain_failure_handoff_last_provider_and_budget():
    out,calls=run_chain({name:('quota' if name in {'zai','exa'} else 'network') for name in WEB_ENGINES},region='both')
    assert calls==list(PROFILE.cn_providers)+list(PROFILE.overseas_providers)
    assert [f['max_results'] for f in out['fallback_requests']]==[3,2]
    for fallback, chain in zip(out['fallback_requests'],(PROFILE.cn_providers,PROFILE.overseas_providers)):
        assert fallback['failed_provider']=='duckduckgo'
        assert fallback['attempted_providers']==list(chain)
    no_quota,_=run_chain({name:'rate_limit' for name in WEB_ENGINES})
    assert not no_quota['fallback_requests']


def test_both_chains_share_ten_results_budget_and_preview_has_no_calls():
    out,calls=run_chain({},region='both',candidates_per_source=10)
    assert calls==['baizhi','exa']
    assert [s['inspected_count'] for s in out['coverage'][0]['scans']]==[1,1]
    out,calls=run_chain({},region='both',dry_run=True)
    assert not calls
    plan=out['plan']['source_plans'][0]
    assert plan['max_discovery_calls']==13
    assert [r['max_candidates'] for r in plan['routes']]==[3,2]


def test_empty_opt_in_and_no_credentials_chain():
    out,calls=run_chain({'baizhi':'empty'},profile=replace(PROFILE,failover_on_empty=True))
    assert calls==['baizhi','zai'] and out['coverage'][0]['scans'][0]['state']=='empty'
    p=WebMaterialProfile(search_provider='chain')
    out,calls=run_chain({},profile=p)
    assert calls==['duckduckgo'] and len(out['coverage'][0]['scans'])==6


def test_chain_settings_defaults_alias_env_and_validation(monkeypatch):
    p=web_material_profile(values={'WEB_MATERIALS_ENABLED':'true','WEB_SEARCH_PROVIDER':'chain','ZAI_CODING_CN_API_KEY':'secret-zai'})
    assert p.zai_api_key=='secret-zai' and p.cn_providers==PROFILE.cn_providers
    assert p.overseas_providers==PROFILE.overseas_providers
    monkeypatch.setenv('WEB_CN_PROVIDERS','exa,duckduckgo')
    monkeypatch.setenv('EXA_API_KEY','invalid-testing-key')
    p=web_material_profile(values={'WEB_MATERIALS_ENABLED':'true','WEB_SEARCH_PROVIDER':'chain'}, process_overrides=True)
    assert p.cn_providers==('exa','duckduckgo') and p.exa_api_key=='invalid-testing-key'
    assert 'invalid-testing-key' not in repr(p)
    for changes in ({'cn_providers':()}, {'cn_providers':('exa','exa')}, {'cn_providers':('anonymous',)},
                    {'exa_search_type':'deep'}, {'failover_on_empty':'false'}):
        with pytest.raises(SourceConfigError): replace(PROFILE,**changes)


def test_exa_lite_and_deep_rejection(monkeypatch):
    from ir_search.adapters.exa import build_exa_payload
    from ir_search.models import Query
    calls=[]
    p=replace(PROFILE,exa_search_type='lite')
    ExaWebClient(p,transport=lambda url,**kw:(calls.append(kw) or _Reply(200,'application/json','',b'{"results":[]}',NOW))).search('NVIDIA',limit=1,context=RequestContext())
    assert json.loads(calls[0]['body'])['type']=='lite'
    monkeypatch.setenv('EXA_SEARCH_TYPE','lite')
    assert build_exa_payload(Query(text='NVIDIA'))['type']=='lite'
    monkeypatch.setenv('EXA_SEARCH_TYPE','deep')
    from ir_search.adapters.base import AdapterError
    with pytest.raises(AdapterError,match='unsupported_exa_search_type'):build_exa_payload(Query(text='NVIDIA'))


@pytest.mark.parametrize('name', NEW)
def test_adapter_input_validation_and_direct_query(name):
    engine=adapter(name,transport_for(name,'ok',[]))
    for query, limit in (('',1),('x',True),('x',11)):
        with pytest.raises(DataAdapterError):engine.search(query,limit=limit,context=RequestContext())
    if name!='tavily':
        from ir_search.models import Query
        assert engine.query(Query(text='NVIDIA earnings',count=3))[0].source==name


def test_mcp_sse_double_encoded_zai_and_unknown_date():
    base=transport_for('zai','ok',[])
    def transport(url,**kw):
        reply=base(url,**kw)
        if kw.get('body') and json.loads(kw['body']).get('method')=='tools/call':
            value=json.loads(reply.body)
            text=value['result']['content'][0]['text']
            value['result']['content'][0]['text']=json.dumps(text)
            return replace(reply,content_type='text/event-stream',body=('event: message\ndata: '+json.dumps(value)+'\n\n').encode())
        return reply
    page=adapter('zai',transport).search('NVIDIA',limit=1,context=RequestContext())
    assert page.rows[0].get('published_at') is None


def test_engine_deadline_keeps_parent_budget_for_next_engine(monkeypatch):
    from ir_search.infrastructure.web_engine import _EngineContext
    clock=[0.0]
    monkeypatch.setattr('ir_search.infrastructure.web_engine.monotonic',lambda:clock[0])
    parent=RequestContext()
    child=_EngineContext(parent)
    child.begin_operation()
    clock[0]=16.0
    with pytest.raises(DataAdapterError,match='timeout'):child.check_active()
    parent.check_active()
    assert parent.operations==1


@pytest.mark.parametrize('name', NEW)
def test_missing_key_and_invalid_profile_are_zero_network(name):
    engine=adapter(name,lambda *a,**kw:pytest.fail('unexpected network'))
    if name=='tavily':engine._client._profile=WebMaterialProfile(search_provider='chain')
    else:engine._profile=WebMaterialProfile(search_provider='chain')
    if name!='duckduckgo':
        with pytest.raises(DataAdapterError,match='no_credentials'):
            engine.search('NVIDIA',limit=1,context=RequestContext())


@pytest.mark.parametrize('tamper', ('omit_attempt','invent_quota_on_success','negative_elapsed'))
def test_chain_handoff_and_duration_cannot_be_forged(tamper):
    calls=[];reg=MaterialRegistry()
    chain=replace(PROFILE,cn_providers=('baizhi','zai'))
    outcomes={'baizhi':'quota','zai':'network'} if tamper!='invent_quota_on_success' else {'baizhi':'quota'}
    a=WebMaterialAdapter(chain,clients={name:Client(name,outcomes,calls) for name in WEB_ENGINES})
    reg.register(a);original=a.search_materials
    def altered(req,**kw):
        page=original(req,**kw)
        if tamper=='omit_attempt':page.fallback_requests[0]=replace(page.fallback_requests[0],attempted_providers=('zai',))
        elif tamper=='negative_elapsed':object.__setattr__(page.scans[0],'elapsed_ms',-1)
        else:page.fallback_requests.append(WebSearchFallback('zai','NVIDIA','cn',req.published_start,req.published_end,3,attempted_providers=('baizhi','zai')))
        return page
    a.search_materials=altered
    out=search_materials(request(web_region='cn'),registry=reg)
    assert not out.fallback_requests and 'invalid_material_response' in {d.code for d in out.diagnostics}


@pytest.mark.parametrize('name', ('exa','bocha','anysearch','tavily'))
def test_legacy_search_errors_are_also_redacted(name,monkeypatch):
    import io, urllib.error
    from ir_search.adapters.base import AdapterError
    from ir_search.models import Query
    module=importlib.import_module('ir_search.adapters.'+name)
    cls=getattr(module,{'exa':'ExaAdapter','bocha':'BochaAdapter','anysearch':'AnySearchAdapter','tavily':'TavilyAdapter'}[name])
    monkeypatch.setenv(name.upper()+'_API_KEY','synthetic-key')
    def failure(*a,**kw):raise urllib.error.HTTPError('https://api.example.com',402,'',{},io.BytesIO(b'{"message":"private-generated-credential"}'))
    monkeypatch.setattr(module,'open_url',failure)
    with pytest.raises(AdapterError) as caught:cls().query(Query(text='NVIDIA'))
    assert str(caught.value)=='quota'
