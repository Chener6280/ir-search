"""Fixed destinations, secret-safe failures, TLS, bounded data and no redirects."""
import io
from email.message import Message
import ssl
import urllib.error

import pytest

from ir_search import RequestContext
from ir_search.infrastructure.search_transport import _search_request, _NoRedirect
from ir_search.registry import DataAdapterError


class Response(io.BytesIO):
    def __init__(self, data=b'{"results":[]}', status=200, content_type='application/json', **headers):
        super().__init__(data)
        self.code=status
        self.headers=Message()
        self.headers['Content-Type']=content_type
        for k,v in headers.items():self.headers[k]=v


def test_fixed_search_transport_reads_existing_proxy_path_and_never_redirects(monkeypatch):
    calls=[]
    class Opener:
        def open(self, req, timeout):
            calls.append((req,timeout))
            return Response()
    monkeypatch.setattr('urllib.request.build_opener',lambda *a:Opener())
    reply=_search_request('https://api.exa.ai/search',context=RequestContext(),body=b'{}',headers={'x-api-key':'fake-secret'})
    assert reply.status==200 and calls[0][0].get_header('X-api-key')=='fake-secret'
    with pytest.raises(DataAdapterError,match='blocked_url'):
        _NoRedirect().redirect_request(None,None,302,'','', 'https://example.com')


@pytest.mark.parametrize('url,method', [('http://api.exa.ai/search','POST'),('https://api.exa.ai/search?q=x','POST'),
    ('https://other.example/search','POST'),('https://api.exa.ai:444/search','POST'),
    ('https://api.search.brave.com/res/v1/web/search','POST'),('https://api.exa.ai/search','GET')])
def test_search_transport_rejects_non_fixed_destinations_without_network(url,method,monkeypatch):
    monkeypatch.setattr('urllib.request.build_opener',lambda *a:pytest.fail('network attempted'))
    with pytest.raises(DataAdapterError,match='blocked_url'):_search_request(url,method=method,context=RequestContext())


@pytest.mark.parametrize('error,expected', [(TimeoutError('private-upstream'),'timeout'),
    (urllib.error.URLError(ssl.SSLError('private-upstream')),'tls_error'),
    (urllib.error.URLError(OSError('private-upstream')),'network')])
def test_transport_failures_cannot_echo_body_or_credentials(monkeypatch,error,expected):
    class Opener:
        def open(self,*a,**kw):raise error
    monkeypatch.setattr('urllib.request.build_opener',lambda *a:Opener())
    with pytest.raises(DataAdapterError) as caught:_search_request('https://api.exa.ai/search',context=RequestContext())
    assert str(caught.value)==expected


def test_bounded_error_body_and_session_headers_are_private(monkeypatch):
    class Opener:
        def open(self,*a,**kw):return Response(b'x'*16385,status=402)
    monkeypatch.setattr('urllib.request.build_opener',lambda *a:Opener())
    with pytest.raises(DataAdapterError,match='response_too_large'):_search_request('https://api.anysearch.com/v1/search',context=RequestContext())
    class MCP:
        def open(self,*a,**kw):return Response(b'',status=202,**{'Mcp-Session-Id':'session-only'})
    monkeypatch.setattr('urllib.request.build_opener',lambda *a:MCP())
    reply=_search_request('https://agent-toolkit.app.baizhi.cloud/mcp',context=RequestContext(),preserve_headers=True)
    assert reply.headers=={'Mcp-Session-Id':'session-only'} and 'session-only' not in repr(reply)
