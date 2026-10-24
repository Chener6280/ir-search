"""Keep TLS verification, explicit challenges, source dates and one-request stopping."""
from dataclasses import replace
from datetime import datetime, timezone
import json
import ssl

import pytest

from ir_search import MaterialRegistry, RequestContext, search_materials
from ir_search.adapters.duckduckgo import DuckDuckGoAdapter
from ir_search.adapters.bocha import BochaAdapter
from ir_search.adapters.web_materials import WebMaterialAdapter
from ir_search.infrastructure.credentials import WebMaterialProfile
from ir_search.infrastructure.public_web import _Reply
from ir_search.infrastructure.web_search import BochaWebClient
from ir_search.infrastructure.search_transport import _search_request
from ir_search.registry import DataAdapterError
from tests.test_search_transport import Response
from tests.test_web_quota_fallback import request

NOW = datetime(2026,10,24,tzinfo=timezone.utc)
PROFILE = WebMaterialProfile(search_provider='bocha',bocha_api_key='synthetic-bocha-key')
CHALLENGE = b'<form id="challenge-form" action="//duckduckgo.com/anomaly.js"><div class="anomaly-modal__box">Verify</div></form>'


def test_bocha_uses_current_official_endpoint_for_sdk_and_compatibility(monkeypatch):
    calls=[]
    def transport(url,**kw):
        calls.append((url,kw))
        return _Reply(200,'application/json','',b'{"code":200,"data":{"webPages":{"value":[]}}}',NOW)
    assert BochaAdapter.endpoint == 'https://api.bocha.cn/v1/web-search'
    page=BochaWebClient(PROFILE,transport=transport).search('earnings',limit=1,context=RequestContext())
    assert not page.rows and len(calls)==1
    url,kw=calls[0]
    assert url==BochaAdapter.endpoint and kw['headers']['Authorization']=='Bearer synthetic-bocha-key'
    assert json.loads(kw['body'])['summary'] is False


@pytest.mark.parametrize('status', (200,202))
def test_duckduckgo_challenge_is_blocked_not_empty_or_quota(status):
    calls=[];ctx=RequestContext()
    def transport(*args,**kwargs):
        calls.append(args)
        return _Reply(status,'text/html','',CHALLENGE,NOW)
    client=DuckDuckGoAdapter(PROFILE,transport=transport)
    with pytest.raises(DataAdapterError) as caught:client.search('earnings',limit=1,context=ctx)
    assert caught.value.code=='web_content_challenge'
    assert caught.value.failure_kind.value=='blocked_by_policy'
    assert len(calls)==1 and not ctx._wait_for_public_host('html.duckduckgo.com')
    assert 'anomaly.js' not in str(caught.value)


def test_challenge_wins_over_embedded_results_and_no_results_marker():
    mixed=CHALLENGE+b'<div class="no-results__message"></div><a class="result__a" href="https://example.com/a">bait</a>'
    client=DuckDuckGoAdapter(PROFILE,transport=lambda *a,**kw:_Reply(200,'text/html','',mixed,NOW))
    with pytest.raises(DataAdapterError,match='web_content_challenge'):client.search('earnings',limit=1,context=RequestContext())


def test_one_ddg_challenge_blocks_further_http_in_same_request(monkeypatch):
    calls=[]
    class Opener:
        def open(self,req,timeout):
            calls.append(req)
            return Response(CHALLENGE,status=202,content_type='text/html')
    monkeypatch.setattr('urllib.request.build_opener',lambda *a:Opener())
    client=DuckDuckGoAdapter(PROFILE);ctx=RequestContext()
    with pytest.raises(DataAdapterError,match='web_content_challenge'):client.search('earnings',limit=1,context=ctx)
    with pytest.raises(DataAdapterError,match='rate_limit'):client.search('earnings',limit=1,context=ctx)
    assert len(calls)==1 and ctx.operations==1
    assert not any('cookie' in k.lower() for k in calls[0].headers)
    assert calls[0].get_header('User-agent')=='ir-search/0.2'


@pytest.mark.parametrize('status,content,expected', [
    (202,b'<html>pending</html>','rate_limit'),
    (429,b'<html>slow down</html>','rate_limit'),
    (200,b'<html>unrecognized</html>','upstream_schema'),
])
def test_non_challenge_failure_semantics_remain_visible(status,content,expected):
    client=DuckDuckGoAdapter(PROFILE,transport=lambda *a,**kw:_Reply(status,'text/html','',content,NOW))
    with pytest.raises(DataAdapterError,match=expected):client.search('earnings',limit=1,context=RequestContext())


def test_challenge_does_not_generate_quota_handoff_and_records_scan():
    profile=replace(PROFILE,search_provider='chain',cn_providers=('duckduckgo',))
    client=DuckDuckGoAdapter(profile,transport=lambda *a,**kw:_Reply(202,'text/html','',CHALLENGE,NOW))
    reg=MaterialRegistry();reg.register(WebMaterialAdapter(profile,clients={'duckduckgo':client}))
    out=search_materials(request(web_region='cn'),registry=reg).to_dict()
    assert not out['items'] and not out['fallback_requests']
    scan=out['coverage'][0]['scans'][0]
    assert scan['discovery_provider']=='duckduckgo' and scan['state']=='web_content_challenge'
    assert scan['elapsed_ms']>=0


def test_certificate_failure_remains_terminal_for_endpoint_and_is_never_retried(monkeypatch):
    import http.client
    calls=[]
    def connect(connection):
        calls.append(connection)
        assert connection._context.verify_mode == ssl.CERT_REQUIRED
        assert connection._context.check_hostname is True
        raise ssl.SSLCertVerificationError('synthetic-upstream-secret')
    monkeypatch.setattr(http.client.HTTPSConnection,'connect',connect)
    with pytest.raises(DataAdapterError,match='tls_error'):
        _search_request('https://api.bocha.cn/v1/web-search',context=RequestContext())
    assert len(calls)==1


def test_duckduckgo_successful_empty_stays_successful():
    client=DuckDuckGoAdapter(PROFILE,transport=lambda *a,**kw:_Reply(200,'text/html','',b'<div class="no-results__message">None</div>',NOW))
    assert not client.search('earnings',limit=1,context=RequestContext()).rows
