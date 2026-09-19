"""Wisburg official MCP transport: TLS, bounded SSE and credential-safe failures."""
from datetime import datetime, timezone
import json
import ssl
import time
from threading import Event, Timer

import pytest

from ir_search.context import RequestContext, RequestStopped
from ir_search.infrastructure import wisburg as rpc
from ir_search.infrastructure.credentials import WisburgProfile
from ir_search.registry import DataAdapterError

PROFILE=WisburgProfile('synthetic_secret_token')
PAYLOAD={'jsonrpc':'2.0','id':1,'method':'initialize','params':{}}


class Response:
    def __init__(self,raw,status=200,headers=None,chunks=None):
        self.raw,self.status=raw,status
        self.headers={'Content-Type':'application/json',**(headers or {})}
        self.chunks=chunks
    def getheader(self,key,default=None):return self.headers.get(key,default)
    def read1(self,size):
        if self.chunks is not None:return self.chunks.pop(0) if self.chunks else b''
        chunk,self.raw=self.raw[:size],self.raw[size:];return chunk


class Socket:
    def settimeout(self,value):pass
    def shutdown(self,*args):pass


def transport(monkeypatch,response):
    instances=[]
    class Connection:
        def __init__(self,host,**kwargs):self.host,self.kwargs,self.sock,self.closed=host,kwargs,Socket(),False;instances.append(self)
        def connect(self):pass
        def request(self,*args,**kwargs):self.args,self.request_kw=args,kwargs
        def getresponse(self):return response
        def close(self):self.closed=True
    monkeypatch.setattr(rpc.http.client,'HTTPSConnection',Connection)
    return instances


def post():return rpc._post(PROFILE,PAYLOAD,session_id='',version='',context=RequestContext())


def test_fixed_official_host_tls_headers_and_session(monkeypatch):
    body=json.dumps({'jsonrpc':'2.0','id':1,'result':{}}).encode()
    instances=transport(monkeypatch,Response(body,headers={'Mcp-Session-Id':'session_1'}))
    reply=post();conn=instances[0]
    assert reply.session_id=='session_1' and conn.host=='mcp.wisburg.com' and conn.args[:2]==('POST','/mcp')
    assert conn.request_kw['headers']['Authorization']=='Bearer '+PROFILE.api_key
    assert PROFILE.api_key not in conn.args[1] and PROFILE.api_key not in repr(reply)
    assert conn.kwargs['context'].verify_mode==ssl.CERT_REQUIRED and conn.kwargs['context'].check_hostname
    assert conn.closed


def test_sse_fragmentation_notifications_and_protocol_validation(monkeypatch):
    raw=b': ping\n\ndata: {"jsonrpc":"2.0","method":"notifications/progress"}\n\ndata: {"jsonrpc":"2.0","id":1,"result":{}}\n\n'
    transport(monkeypatch,Response(b'',headers={'Content-Type':'text/event-stream'},chunks=[raw[:55],raw[55:]]))
    assert post().payload['result']=={}
    transport(monkeypatch,Response(b'',status=202))
    assert rpc._post(PROFILE,{'jsonrpc':'2.0','method':'notifications/initialized'},session_id='',version='',context=RequestContext()).payload=={}


@pytest.mark.parametrize('status,code',[(301,'entitlement_denied'),(307,'entitlement_denied'),(401,'authentication_failed'),(403,'entitlement_denied'),(429,'rate_limit'),(503,'network')])
def test_status_errors_never_expose_body(monkeypatch,status,code):
    transport(monkeypatch,Response(PROFILE.api_key.encode(),status=status))
    with pytest.raises(DataAdapterError,match=code) as exc:post()
    assert PROFILE.api_key not in str(exc.value)


@pytest.mark.parametrize('raw,headers',[
    (b'not json',{}),
    (b'{"jsonrpc":"2.0","id":2,"result":{}}',{}),
    (b'{"jsonrpc":"2.0","id":1,"method":"sampling/createMessage"}',{}),
    (b'{"jsonrpc":"2.0","id":1,"result":{"x":NaN}}',{}),
    (b'{}',{'Content-Encoding':'gzip'}),
    (b'{}',{'Content-Type':'text/html'}),
    (b'{}',{'Mcp-Session-Id':'bad\nheader'}),
    (b'{}',{'Content-Length':'999999999'}),
    (json.dumps({'jsonrpc':'2.0','id':1,'result':{'echo':PROFILE.api_key}}).encode(),{}),
])
def test_invalid_and_oversized_wire_data_are_sanitized(monkeypatch,raw,headers):
    transport(monkeypatch,Response(raw,headers=headers))
    with pytest.raises(DataAdapterError) as exc:post()
    assert PROFILE.api_key not in str(exc.value)


def test_pre_cancelled_and_tls_failure(monkeypatch):
    context=RequestContext();context.cancel()
    def fail(*a,**kw):raise ssl.SSLError('secret '+PROFILE.api_key)
    monkeypatch.setattr(rpc.http.client,'HTTPSConnection',fail)
    with pytest.raises(DataAdapterError,match='tls_error'):post()
    with pytest.raises(RequestStopped):rpc._post(PROFILE,PAYLOAD,session_id='',version='',context=context)


def test_cancel_closes_blocked_response_without_waiting_for_deadline(monkeypatch):
    released=Event()
    class BlockingResponse(Response):
        def read1(self,size):
            assert released.wait(2), 'Cancellation did not interrupt the socket'
            raise OSError('closed')
    connections=transport(monkeypatch,BlockingResponse(b''))
    monkeypatch.setattr(Socket,'shutdown',lambda *args:released.set())
    context=RequestContext(timeout_seconds=10)
    timer=Timer(0.1,context.cancel);timer.start()
    try:
        with pytest.raises(RequestStopped,match='cancelled'):
            rpc._post(PROFILE,PAYLOAD,session_id='',version='',context=context)
    finally:timer.cancel()
    assert released.is_set() and connections[0].closed
