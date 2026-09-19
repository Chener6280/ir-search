"""No real network: exercise public-address pinning, limits, redirects and parsing."""
from datetime import datetime, timezone
import socket
import ssl
import time
from threading import Event

import pytest

from ir_search import RequestContext
from ir_search.context import RequestStopped
from ir_search.infrastructure import public_web as web
from ir_search.registry import DataAdapterError

NOW=datetime(2026,9,15,tzinfo=timezone.utc)
ADDRESS=(socket.AF_INET,socket.SOCK_STREAM,6,"",("93.184.216.34",443))


class Socket:
    def __init__(self): self.stopped=Event(); self.address=None
    def settimeout(self,n): assert n>0
    def connect(self,address): self.address=address
    def shutdown(self,*args): self.stopped.set()


class Response:
    def __init__(self,body=b"<p>public text</p>",status=200,headers=None):
        self.body=body; self.status=status; self.headers={"Content-Type":"text/html; charset=utf-8",**(headers or {})}
    def getheader(self,name,default=None): return self.headers.get(name,default)
    def read1(self,size):
        part,self.body=self.body[:size],self.body[size:]; return part


class Connection:
    def __init__(self,response): self.response=response; self.sock=Socket(); self.requests=[]; self.closed=False
    def connect(self): pass
    def request(self,method,target,body,headers): self.requests.append((method,target,body,headers))
    def getresponse(self): return self.response
    def close(self): self.closed=True


def fixture(monkeypatch,response):
    conn=Connection(response)
    monkeypatch.setattr(web,"_resolve",lambda *args:ADDRESS)
    def factory(host,port,**kw):
        assert kw['address']==ADDRESS
        if port==443:
            assert kw['tls'].check_hostname and kw['tls'].verify_mode==ssl.CERT_REQUIRED
            assert kw['tls'].minimum_version>=ssl.TLSVersion.TLSv1_2
        return conn
    monkeypatch.setattr(web,"_Connection",factory)
    return conn


def test_http_429_stops_same_host_without_retry_or_second_dns(monkeypatch):
    conn = fixture(monkeypatch, Response(status=429, headers={'Retry-After': '10'}))
    context = RequestContext()
    with pytest.raises(DataAdapterError, match='rate_limit'):
        web._request('https://example.test/a', context=context)
    monkeypatch.setattr(web, '_resolve', lambda *a: pytest.fail('retry after 429'))
    with pytest.raises(DataAdapterError, match='rate_limit'):
        web._request('https://example.test/b', context=context)
    assert len(conn.requests) == context.operations == 1


def test_search_api_error_body_is_bounded_and_does_not_change_page_policy(monkeypatch):
    conn=fixture(monkeypatch,Response(body=b'{"message":"quota exhausted"}',status=403,
                                    headers={'Content-Type':'application/json'}))
    reply=web._request('https://api.bochaai.com/v1/web-search',context=RequestContext(),method='POST',search_api_errors=True)
    assert reply.status==403 and b'quota exhausted' in reply.body and conn.closed
    fixture(monkeypatch,Response(body=b'x'*16385,status=403))
    with pytest.raises(DataAdapterError,match='response_too_large'):
        web._request('https://api.exa.ai/search',context=RequestContext(),method='POST',search_api_errors=True)
    for url,method in [('https://example.com/','POST'),('https://api.exa.ai/search','GET'),
                       ('http://api.exa.ai/search','POST'),('https://api.exa.ai/search?x=y','POST')]:
        with pytest.raises(DataAdapterError,match='blocked_url'):
            web._request(url,context=RequestContext(),method=method,search_api_errors=True)


def test_search_api_429_still_stops_host_after_returning_error_body(monkeypatch):
    conn=fixture(monkeypatch,Response(body=b'{}',status=429,headers={'Content-Type':'application/json'}))
    context=RequestContext()
    assert web._request('https://api.exa.ai/search',context=context,method='POST',search_api_errors=True).status==429
    with pytest.raises(DataAdapterError,match='rate_limit'):
        web._request('https://api.exa.ai/search',context=context,method='POST',search_api_errors=True)
    assert len(conn.requests)==1


@pytest.mark.parametrize("url",["file:///etc/passwd","http://127.0.0.1/a","http://169.254.169.254/a","http://[::1]/",
    "http://localhost/a","https://service.local/a","https://user:secret@example.test/","https://example.test/?api_key=secret",
    "https://example.test:8443/","https://example.test/\nheader","https://example.test\\@evil.test/","https://localhost./"])
def test_unsafe_urls_fail_before_network(monkeypatch,url):
    monkeypatch.setattr(web,"_resolve",lambda *a:pytest.fail("unexpected DNS"))
    with pytest.raises(DataAdapterError,match="blocked_url"): web.fetch_public_document(url,context=RequestContext())


@pytest.mark.parametrize("ip",["127.0.0.1","10.1.2.3","169.254.1.1","::1","192.168.1.1"])
def test_dns_rejects_private_answers_including_mixed_answer_sets(monkeypatch,ip):
    monkeypatch.setattr(socket,"getaddrinfo",lambda *a,**kw:[ADDRESS,(socket.AF_INET,socket.SOCK_STREAM,6,"",(ip,443))])
    with pytest.raises(DataAdapterError,match="blocked_url"): web._resolve("public-looking.test",443,RequestContext())


def test_connection_uses_checked_numeric_address_with_original_tls_hostname(monkeypatch):
    sock=Socket(); wrapped=[]
    monkeypatch.setattr(socket,"socket",lambda *args:sock)
    monkeypatch.setattr(socket,"getaddrinfo",lambda *args,**kw:pytest.fail("second DNS lookup"))
    class TLS:
        def wrap_socket(self,sock,server_hostname): wrapped.append(server_hostname); return sock
    conn=web._Connection("official.test",443,address=ADDRESS,tls=TLS(),timeout=1)
    conn.connect()
    assert sock.address==ADDRESS[4] and wrapped==["official.test"]


@pytest.mark.parametrize('tls,port,host,expected', [
    (None, 80, 'official.test', b'Host: official.test\r\n'),
    (object(), 443, 'official.test', b'Host: official.test\r\n'),
    (object(), 443, '2606:4700::1111', b'Host: [2606:4700::1111]\r\n'),
])
def test_pinned_connection_sends_scheme_default_host_without_port(tls, port, host, expected):
    # Exercise stdlib request serialization: a mocked _Connection misses this bug.
    class CaptureSocket:
        def __init__(self): self.written = bytearray()
        def sendall(self, value): self.written.extend(value)
    conn = web._Connection(host, port, address=ADDRESS, tls=tls, timeout=1)
    conn.sock = CaptureSocket()
    conn.request('GET', '/article')
    assert expected in bytes(conn.sock.written)
    assert bytes(conn.sock.written).count(b'Host:') == 1


def test_request_passes_no_ambient_auth_and_closes_connection(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY","http://127.0.0.1:8888")
    monkeypatch.setenv("ANYSEARCH_API_KEY","should_not_go_to_origin")
    conn=fixture(monkeypatch,Response())
    reply=web._request("https://example.test/article?q=中文#fragment",context=RequestContext())
    assert reply.status==200 and conn.closed
    method,target,body,headers=conn.requests[0]
    assert method=="GET" and "fragment" not in target and "%E4%B8%AD" in target
    assert "Authorization" not in headers and "Cookie" not in headers


@pytest.mark.parametrize("response,code",[
    (Response(status=403),"entitlement_denied"),(Response(status=404),"not_found"),(Response(status=429),"rate_limit"),
    (Response(body=b"x"*9),"response_too_large"),(Response(headers={"Content-Length":"9"}),"response_too_large"),
    (Response(headers={"Content-Length":"wrong"}),"upstream_schema"),
    (Response(headers={"Content-Encoding":"gzip"}),"web_content_unsupported")])
def test_http_limits_and_failure_codes(monkeypatch,response,code):
    conn=fixture(monkeypatch,response)
    with pytest.raises(DataAdapterError,match=code):web._request("https://example.test/a",context=RequestContext(),max_bytes=8)
    assert conn.closed


def test_redirects_checked_for_each_target_and_domain_restrictions(monkeypatch):
    calls=[]
    def request(url,**kw):
        calls.append(url)
        if len(calls)==1:return web._Reply(302,"","https://www.example.gov.cn/final",b"",NOW)
        return web._Reply(200,"text/html","",b"<title>Article</title><p>Original text.</p>",NOW)
    monkeypatch.setattr(web,"_request",request)
    doc=web.fetch_public_document("https://example.gov.cn/start",context=RequestContext(),allowed_domains=("gov.cn",))
    assert doc.url.endswith("/final") and doc.extra["redirects"]==1 and len(calls)==2
    calls.clear()
    monkeypatch.setattr(web,"_request",lambda url,**kw:(calls.append(url) or web._Reply(302,"","http://127.0.0.1/private",b"",NOW)))
    with pytest.raises(DataAdapterError,match="blocked_url"): web.fetch_public_document("https://example.gov.cn/start",context=RequestContext())
    assert len(calls)==1
    monkeypatch.setattr(web,"_request",lambda url,**kw:web._Reply(302,"","https://outside.test/",b"",NOW))
    with pytest.raises(DataAdapterError,match="blocked_url"):web.fetch_public_document("https://example.gov.cn/start",context=RequestContext(),allowed_domains=("gov.cn",))


@pytest.mark.parametrize("location,code",[("/start","web_redirect_limit"),("http://example.test/downgrade","blocked_url"),
    ("https://example.test/?token=secret","blocked_url"),("","web_redirect_limit")])
def test_redirect_loop_downgrade_credential_and_missing_location(monkeypatch,location,code):
    monkeypatch.setattr(web,"_request",lambda *a,**kw:web._Reply(302,"",location,b"",NOW))
    with pytest.raises(DataAdapterError,match=code):web.fetch_public_document("https://example.test/start",context=RequestContext())


def test_html_charset_plaintext_and_truncation(monkeypatch):
    html='<meta name="pubdate" content="2026-09-14"><title>政策</title><script>hidden</script><p>智能家居政策文本</p>'
    monkeypatch.setattr(web,"_request",lambda *a,**kw:web._Reply(200,"text/html; charset=gb2312","",html.encode("gb18030"),NOW))
    doc=web.fetch_public_document("https://example.gov.cn/a",context=RequestContext(),max_chars=8)
    assert "hidden" not in doc.text and "�" not in doc.text and len(doc.text)<=8 and doc.warnings
    assert doc.published_at.date().isoformat()=="2026-09-14"
    monkeypatch.setattr(web,"_request",lambda *a,**kw:web._Reply(200,"text/plain","",b"literal <b>text</b>",NOW))
    doc=web.fetch_public_document("http://example.test/a",context=RequestContext())
    assert "<b>text</b>" in doc.text and "unencrypted_public_document" in doc.warnings


@pytest.mark.parametrize("mime,body,code",[("application/zip",b"binary","web_content_unsupported"),
    ("text/html; charset=unknown",b"a","web_content_unsupported"),("text/html",b"\xff","upstream_schema"),
    ("text/html",b"<script>hidden</script>","no_extracted_text")])
def test_unsupported_mime_encoding_empty_and_invalid_text(monkeypatch,mime,body,code):
    monkeypatch.setattr(web,"_request",lambda *a,**kw:web._Reply(200,mime,"",body,NOW))
    with pytest.raises(DataAdapterError,match=code):web.fetch_public_document("https://example.test/a",context=RequestContext())


def test_timeout_errors_and_cancellation_do_not_leak_raw_details(monkeypatch):
    conn=fixture(monkeypatch,Response())
    conn.connect=lambda:(_ for _ in ()).throw(ssl.SSLError("secret"))
    with pytest.raises(DataAdapterError,match="tls_error") as caught:web._request("https://example.test/a",context=RequestContext())
    assert "secret" not in str(caught.value) and conn.closed
    conn=fixture(monkeypatch,Response())
    def read(size):
        conn.sock.stopped.wait(2)
        raise OSError("private detail")
    conn.response.read1=read
    start=time.monotonic()
    with pytest.raises(RequestStopped,match="deadline_exceeded"):
        web._request("https://example.test/a",context=RequestContext(timeout_seconds=0.08))
    assert time.monotonic()-start<1 and conn.closed


def test_document_argument_validation():
    for options in [dict(max_chars=True),dict(max_chars=100001),dict(allowed_domains="gov.cn")]:
        with pytest.raises(ValueError):web.fetch_public_document("https://example.test/a",context=RequestContext(),**options)


def test_ima_scoped_download_headers_accept_official_empty_category(monkeypatch):
    conn=fixture(monkeypatch,Response(body=b'file',headers={'Content-Type':'application/octet-stream'}))
    headers={'X-IMA-Create-URL-Time':'1789516800','X-IMA-Platform':'00','X-IMA-Resource-Category':'',
             'X-IMA-Sign':'temporary-signature','X-IMA-Trace-ID':'trace','X-IMA-UID-SHA256':'hash'}
    reply=web._request('https://res-pkb.ima.qq.com/file?signature=temporary',context=RequestContext(),
                       signed_download=True,scoped_download_headers=headers)
    assert reply.body==b'file' and conn.closed
    sent=conn.requests[0][3]
    assert all(sent[k]==v for k,v in headers.items())
    assert not any(k.lower().startswith('ima-openapi') for k in sent)


def test_signed_download_cannot_mix_scoped_and_api_headers(monkeypatch):
    monkeypatch.setattr(web,'_resolve',lambda *a:pytest.fail('network reached'))
    for options in (dict(headers={'Authorization':'secret'}),dict(method='POST'),dict(body=b'secret')):
        with pytest.raises(DataAdapterError,match='blocked_url'):
            web._request('https://res-pkb.ima.qq.com/file',context=RequestContext(),signed_download=True,
                         scoped_download_headers={'X-IMA-Sign':'temporary'},**options)
