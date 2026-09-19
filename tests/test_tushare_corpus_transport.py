"""Exercise real protocol framing against in-memory HTTP responses, without keys."""
import json
import socket
import ssl
import time
from threading import Event

import pytest

from ir_search.context import RequestContext, RequestStopped
from ir_search.infrastructure.credentials import TushareCorpusProfile
from ir_search.infrastructure import tushare_corpus as protocol
from ir_search.registry import DataAdapterError

PROFILE = TushareCorpusProfile("synthetic_secret_key")
PAYLOAD = {"jsonrpc":"2.0","id":7,"method":"tools/call","params":{}}


class Response:
    def __init__(self, content=b'', *, status=200, content_type="application/json", headers=None, chunk_size=65536):
        self.content=content; self.status=status; self.chunk_size=chunk_size
        self.headers={"Content-Type":content_type, **(headers or {})}
    def getheader(self,name,default=None):return self.headers.get(name,default)
    def read1(self,size):
        size=min(size,self.chunk_size)
        data,self.content=self.content[:size],self.content[size:]
        return data


class Socket:
    def __init__(self):self.closed=Event()
    def settimeout(self,value):assert 0<value<=300
    def shutdown(self,*args):self.closed.set()


class Connection:
    def __init__(self,response):self.sock=Socket(); self.response=response; self.closed=False; self.requests=[]
    def connect(self):pass
    def request(self,method,path,body,headers):self.requests.append((method,path,json.loads(body),headers))
    def getresponse(self):return self.response
    def close(self):self.closed=True


def fixture(monkeypatch,response):
    connection=Connection(response)
    def factory(host,**kwargs):
        assert host=="api.tushare.pro"
        assert kwargs["context"].verify_mode==ssl.CERT_REQUIRED and kwargs["context"].check_hostname
        assert kwargs["context"].minimum_version>=ssl.TLSVersion.TLSv1_2
        return connection
    monkeypatch.setattr(protocol.http.client,"HTTPSConnection",factory)
    return connection


def call(context=None):
    return protocol._post(PROFILE,PAYLOAD,session_id="session",version="2025-03-26",context=context or RequestContext())


def test_json_and_official_post_headers_and_connection_cleanup(monkeypatch):
    response={"jsonrpc":"2.0","id":7,"result":{"content":[]}}
    conn=fixture(monkeypatch,Response(json.dumps(response).encode()))
    assert call().payload==response and conn.closed
    method,path,body,headers=conn.requests[0]
    assert method=="POST" and path=="/mcp/?token="+PROFILE.token and body==PAYLOAD
    assert headers["Accept"]=="application/json, text/event-stream" and headers["Mcp-Session-Id"]=="session"


@pytest.mark.parametrize("chunk_size",[5,65536])
def test_sse_long_lines_multiline_utf8_and_notification_before_response(monkeypatch,chunk_size):
    message={"jsonrpc":"2.0","id":7,"result":{"content":[{"type":"text","text":"材料"*35000}]}}
    notice=b'data: {"jsonrpc":"2.0","method":"notifications/progress"}\r\n\r\n'
    raw=json.dumps(message,ensure_ascii=False).encode()
    data=notice+b': comment\nevent: message\ndata: '+raw+b'\n\n'
    conn=fixture(monkeypatch,Response(data,content_type="text/event-stream",chunk_size=chunk_size))
    assert call().payload==message and conn.closed
    multiline=b'\n'.join(b'data: '+line for line in json.dumps(message,ensure_ascii=False,indent=2).encode().splitlines())+b'\n\n'
    fixture(monkeypatch,Response(multiline,content_type="text/event-stream"))
    assert call().payload==message


@pytest.mark.parametrize("response,code",[
    (Response(status=302),"entitlement_denied"),(Response(status=401),"authentication_failed"),
    (Response(status=403),"entitlement_denied"),(Response(status=429),"rate_limit"),(Response(status=503),"network"),
    (Response(b'{broken'),"upstream_schema"),(Response(b'{}',content_type="text/html"),"upstream_schema"),
    (Response(b'{}',headers={"Content-Encoding":"gzip"}),"upstream_schema"),
    (Response(b'{}',headers={"Mcp-Session-Id":"bad\r\nheader"}),"upstream_schema"),
    (Response(b'{"jsonrpc":"2.0","id":8,"result":{}}'),"upstream_schema"),
    (Response(b'{"jsonrpc":"2.0","id":7,"result":{"secret":"synthetic_secret_key"}}'),"upstream_schema"),
    (Response(b'{"jsonrpc":"2.0","id":7,"result":{"v":NaN}}'),"upstream_schema"),
    (Response(b'data: {"jsonrpc":"2.0","id":7,"method":"sampling/createMessage"}\n\n',content_type="text/event-stream"),"unsupported"),
])
def test_transport_errors_never_echo_credentials_or_follow_redirects(monkeypatch,response,code):
    conn=fixture(monkeypatch,response)
    with pytest.raises(DataAdapterError,match=code) as caught:call()
    assert PROFILE.token not in str(caught.value) and conn.closed and len(conn.requests)==1


@pytest.mark.parametrize("headers",[{}, {"Content-Length":"10000"}])
def test_response_byte_limits(monkeypatch,headers):
    monkeypatch.setattr(protocol,"_MAX_BYTES",80)
    conn=fixture(monkeypatch,Response(b' '*81,headers=headers))
    with pytest.raises(DataAdapterError,match="tushare_response_too_large"):call()
    assert conn.closed


@pytest.mark.parametrize("error,code",[(ssl.SSLError("synthetic_secret_key"),"tls_error"),(socket.timeout("synthetic_secret_key"),"timeout"),(OSError("synthetic_secret_key"),"network")])
def test_connection_errors_are_sanitized(monkeypatch,error,code):
    conn=fixture(monkeypatch,Response())
    conn.connect=lambda:(_ for _ in ()).throw(error)
    with pytest.raises(DataAdapterError,match=code) as caught:call()
    assert PROFILE.token not in str(caught.value) and conn.closed


def test_deadline_interrupts_an_open_blocking_read(monkeypatch):
    response=Response()
    conn=fixture(monkeypatch,response)
    def blocking(size):
        conn.sock.closed.wait(2)
        raise OSError("interrupted")
    response.read1=blocking
    started=time.monotonic()
    with pytest.raises(RequestStopped,match="deadline_exceeded"):call(RequestContext(timeout_seconds=0.08))
    assert time.monotonic()-started<1 and conn.closed
