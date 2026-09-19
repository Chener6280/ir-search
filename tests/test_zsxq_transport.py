"""Official MCP wire, allowlist and credential isolation with synthetic responses."""
from datetime import datetime, timezone
import json
import ssl
import time
from threading import Event, Timer

import pytest

from ir_search.context import RequestContext, RequestStopped
from ir_search.infrastructure import zsxq as rpc
from ir_search.infrastructure.credentials import ZsxqProfile
from ir_search.registry import DataAdapterError

PROFILE=ZsxqProfile('synthetic_secret_token')
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
    assert reply.session_id=='session_1' and conn.host=='mcp.zsxq.com' and conn.args[:2]==('POST','/topic/')
    assert conn.request_kw['headers']['Authorization']=='Bearer '+PROFILE.token
    assert PROFILE.token not in conn.args[1] and PROFILE.token not in repr(reply)
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
    transport(monkeypatch,Response(PROFILE.token.encode(),status=status))
    with pytest.raises(DataAdapterError,match=code) as exc:post()
    assert PROFILE.token not in str(exc.value)


@pytest.mark.parametrize('raw,headers',[
    (b'not json',{}),
    (b'{"jsonrpc":"2.0","id":2,"result":{}}',{}),
    (b'{"jsonrpc":"2.0","id":1,"method":"sampling/createMessage"}',{}),
    (b'{"jsonrpc":"2.0","id":1,"result":{"x":NaN}}',{}),
    (b'{}',{'Content-Encoding':'gzip'}),
    (b'{}',{'Content-Type':'text/html'}),
    (b'{}',{'Mcp-Session-Id':'bad\nheader'}),
    (b'{}',{'Content-Length':'999999999'}),
    (json.dumps({'jsonrpc':'2.0','id':1,'result':{'echo':PROFILE.token}}).encode(),{}),
])
def test_invalid_and_oversized_wire_data_are_sanitized(monkeypatch,raw,headers):
    transport(monkeypatch,Response(raw,headers=headers))
    with pytest.raises(DataAdapterError) as exc:post()
    assert PROFILE.token not in str(exc.value)


def test_pre_cancelled_and_tls_failure(monkeypatch):
    context=RequestContext();context.cancel()
    def fail(*a,**kw):raise ssl.SSLError('secret '+PROFILE.token)
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


def make_client(responses=None):
    calls=[]
    def exchange(profile,payload,**kwargs):
        calls.append(payload)
        if payload['method']=='initialize':result={'protocolVersion':'2024-11-05'}
        elif payload['method']=='notifications/initialized':return rpc._RPCResponse({})
        else:
            name=payload['params']['name']
            data=(responses or {}).get(name,{'success':True})
            result={'content':[{'type':'text','text':json.dumps(data)}]}
        return rpc._RPCResponse({'jsonrpc':'2.0','id':payload['id'],'result':result},'session')
    return rpc.ZsxqClient(PROFILE,transport=exchange),calls


def test_client_handshake_groups_and_no_hidden_discovery_or_retries():
    client,calls=make_client({'get_self_info':{'success':True,'user':{'user_id':'100'}},'get_user_groups':{'success':True,'groups':[{'group_id':'200'}]}})
    result=client.groups(context=RequestContext())
    assert result.data['groups'][0]['group_id']=='200'
    assert [c['method'] for c in calls]==['initialize','notifications/initialized','tools/call','tools/call']
    assert PROFILE.token not in repr(result)


@pytest.mark.parametrize('name,args',[
    ('create_topic',{'group_id':'100','text':'write'}),('search_topics',{'group_id':'100','query':'AI'}),
    ('get_group_topics',{'group_id':'100','scope':'all','limit':31}),
    ('get_group_topics',{'group_id':'100','scope':'all','limit':True}),
    ('get_group_topics',{'group_id':'100','scope':'all','limit':1,'end_time':'2026-09-01'}),
    ('get_topic_info',{'topic_id':'1/../../self'}),('get_topic_info',{'topic_id':'101','extra':'anything'}),
    ('call_zsxq_api',{'method':'POST','path':'/v2/groups/100/topics'}),
    ('call_zsxq_api',{'method':'GET','path':'/v3/users/self'}),
    ('call_zsxq_api',{'method':'GET','path':'/v2/files/101/download_url','body':{}}),
])
def test_public_read_rejects_unapproved_tools_and_arguments_before_io(name,args):
    client,calls=make_client()
    with pytest.raises(DataAdapterError,match='unsupported'):client.read(name,args,context=RequestContext())
    assert not calls


def test_business_failure_or_echo_cannot_become_success():
    for data in [{'success':False,'message':'permission denied '+PROFILE.token},{'success':True,'echo':PROFILE.token},{}]:
        client,_=make_client({'get_topic_info':data})
        with pytest.raises(DataAdapterError) as exc:client.read('get_topic_info',{'topic_id':'101'},context=RequestContext())
        assert PROFILE.token not in str(exc.value)
