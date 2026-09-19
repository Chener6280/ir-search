"""Fixed-origin transport and optional worker lifecycle, without network/browser."""
import io
import json
from pathlib import Path
import ssl

import pytest

from ir_search.context import RequestContext, RequestStopped
from ir_search.infrastructure import gangtise as g
from ir_search.registry import DataAdapterError


class Socket:
    def settimeout(self, value): pass
    def shutdown(self, value): pass


class Response:
    def __init__(self, raw=b'{"status": true}', status=200, headers=None):
        self.status=status;self.data=io.BytesIO(raw);self.headers={'Content-Type':'application/json',**(headers or {})}
    def getheader(self,k,default=None):return self.headers.get(k,default)
    def read1(self,n):return self.data.read(n)


class Connection:
    def __init__(self, response):self.response=response;self.sock=Socket();self.calls=[];self.closed=False
    def connect(self):pass
    def request(self,*a,**kw):self.calls.append((a,kw))
    def getresponse(self):return self.response
    def close(self):self.closed=True


def install(monkeypatch,response):
    c=Connection(response);hosts=[]
    monkeypatch.setattr(g.http.client,'HTTPSConnection',lambda host,**kw:hosts.append((host,kw)) or c)
    return c,hosts


def test_https_host_authorization_body_and_connection_closed(monkeypatch):
    c,hosts=install(monkeypatch,Response())
    r=g._http('/application/keysearch/global/search',{'keyword':'茅台'},'test_bearer',context=RequestContext())
    assert r['body']['status'] is True and c.closed
    assert hosts[0][0]=='open.gangtise.com'
    assert hosts[0][1]['context'].verify_mode==ssl.CERT_REQUIRED
    a,kw=c.calls[0]
    assert a[0]=='POST' and kw['headers']['Authorization']=='Bearer test_bearer'
    assert b'test_bearer' not in kw['body']


@pytest.mark.parametrize('response,expected',[(Response(status=302,headers={'Location':'https://evil.invalid'}),'blocked_url'),
    (Response(status=401),'authentication_failed'),(Response(status=429),'rate_limit'),
    (Response(headers={'Content-Encoding':'gzip'}),'upstream_schema'),
    (Response(headers={'Content-Type':'text/html'}),'upstream_schema'),
    (Response(headers={'Content-Length':str(g.MAX_BYTES+1)}),'response_too_large'),
    (Response(raw=b'x'*(g.MAX_BYTES+1)),'response_too_large'),
    (Response(raw=b'not-json'),'upstream_schema')])
def test_redirect_encoding_size_and_schema_failures_are_safe(monkeypatch,response,expected):
    c,hosts=install(monkeypatch,response)
    with pytest.raises(DataAdapterError,match=expected):g._http('/x',None,'synthetic_token',context=RequestContext())
    assert c.closed and len(hosts)==1


def test_download_json_quota_is_not_document_text(monkeypatch):
    install(monkeypatch,Response(raw=b'{"code":903301}',headers={'Content-Type':'text/plain'}))
    with pytest.raises(DataAdapterError,match='quota'):g._http('/x',None,'synthetic_token',context=RequestContext(),text=True)
    install(monkeypatch,Response(raw='<p>测试纪要</p>'.encode(),headers={'Content-Type':'text/html'}))
    assert g._http('/x',None,'synthetic_token',context=RequestContext(),text=True)=='<p>测试纪要</p>'
    install(monkeypatch,Response(raw=b'<form><input type="password"></form>',headers={'Content-Type':'text/html'}))
    with pytest.raises(DataAdapterError,match='authentication_failed'):
        g._http('/x',None,'synthetic_token',context=RequestContext(),text=True)


def test_cancelled_before_connect_and_raw_network_exception_hidden(monkeypatch):
    c,_=install(monkeypatch,Response());ctx=RequestContext();ctx.cancel()
    with pytest.raises(RequestStopped,match='cancelled'):g._http('/x',None,'secret_token',context=ctx)
    assert not c.calls
    def fail():raise RuntimeError('secret_token must never escape')
    c.connect=fail
    with pytest.raises(DataAdapterError) as exc:g._http('/x',None,'secret_token',context=RequestContext())
    assert str(exc.value)=='network'


def test_worker_import_failure_is_sanitized(tmp_path,monkeypatch):
    import builtins
    from ir_search.infrastructure import _gangtise_auth_worker as worker
    real_import=builtins.__import__
    def missing(name,*a,**kw):
        if name.startswith('playwright'):raise ImportError('private_data')
        return real_import(name,*a,**kw)
    monkeypatch.setattr(builtins,'__import__',missing)
    # This path cannot launch a browser or expose an exception.
    import os
    previous=os.umask(0o077)
    try:worker.main()
    finally:os.umask(previous)


def test_auth_worker_parent_uses_private_stdin_and_installed_package(monkeypatch,tmp_path):
    import importlib.util
    import os
    from dataclasses import replace
    from ir_search.infrastructure import gangtise_auth as auth
    from tests.test_gangtise_materials import PROFILE
    state=auth._State(replace(PROFILE,state_dir=str(tmp_path/'state')))
    profile=replace(PROFILE,state_dir=str(tmp_path/'state'))
    captured={}
    class Input(io.BytesIO):
        def close(self):
            payload=json.loads(self.getvalue());captured['payload']=payload
            Path(payload['output']).write_text(json.dumps({'token':'synthetic_browser_token'}))
            super().close()
    class Process:
        def __init__(self,*args,**kw):
            captured.update(argv=args[0],kwargs=kw);self.stdin=Input()
        def poll(self):return 0
    monkeypatch.setattr(importlib.util,'find_spec',lambda name:object())
    monkeypatch.setattr(auth.subprocess,'Popen',Process);monkeypatch.setattr(auth,'_stop',lambda p:captured.update(stopped=True))
    monkeypatch.setenv('UNRELATED_API_KEY','do_not_copy')
    token=auth._browser_login(profile,state,context=RequestContext(),interactive=False)
    assert token=='synthetic_browser_token' and captured['stopped']
    assert profile.phone not in repr(captured['argv']) and profile.password not in repr(captured['argv'])
    assert 'UNRELATED_API_KEY' not in captured['kwargs']['env']
    assert '-I' in captured['argv'] and not Path(captured['payload']['output']).exists()
    assert state.read()['token']==token
