"""Read-only loopback transport and credential containment, entirely offline."""
from io import BytesIO
import json
import socket

import pytest

from ir_search.context import RequestContext, RequestStopped
from ir_search.infrastructure.xhs import MAX_BYTES, XhsClient, XhsProfile
from ir_search.registry import DataAdapterError


TOKEN = 'synthetic-xhs-service-token'


class Reply:
    def __init__(self, status=200, data=None, headers=None, raw=None):
        self.status = status
        self.headers = {'Content-Type': 'application/json; charset=utf-8', **(headers or {})}
        self.stream = BytesIO(raw if raw is not None else json.dumps(
            data if data is not None else {'success': True, 'data': {'is_logged_in': True}}).encode())
    def getheader(self, name, default=None): return self.headers.get(name, default)
    def read(self, length): return self.stream.read(length)


def wire(monkeypatch, tmp_path, reply=None, error=None):
    connections = []
    class Connection:
        sock = None
        def __init__(self, host, port, timeout):
            self.host, self.port, self.timeout = host, port, timeout
            self.closed = False
            connections.append(self)
        def request(self, method, path, body, headers):
            self.method, self.path, self.body, self.headers = method, path, body, headers
            if error: raise error
        def getresponse(self): return reply or Reply()
        def close(self): self.closed = True
    monkeypatch.setattr('ir_search.infrastructure.xhs.http.client.HTTPConnection', Connection)
    return XhsClient(XhsProfile(token=TOKEN, cache_dir=str(tmp_path/'cache'))), connections


def test_fixed_loopback_and_bearer_with_deadline(monkeypatch, tmp_path):
    monkeypatch.setenv('HTTP_PROXY', 'http://example.invalid:3128')
    client, connections = wire(monkeypatch, tmp_path)
    ctx = RequestContext(timeout_seconds=10)
    assert client.check_login(context=ctx) == {'logged_in': True}
    c = connections[0]
    assert (c.host, c.port, c.method, c.path) == ('127.0.0.1', 18060, 'GET', '/api/v1/login/status')
    assert c.headers['Authorization'] == 'Bearer '+TOKEN
    assert 0 < c.timeout <= 10 and ctx.operations == 1 and c.closed


@pytest.mark.parametrize('method,path', [('POST','/api/v1/publish'), ('POST','/api/v1/feeds/comment'),
    ('DELETE','/api/v1/login/cookies'), ('GET','http://example.invalid/'), ('POST','/api/v1/login/status')])
def test_write_and_other_endpoints_never_connected(monkeypatch, tmp_path, method, path):
    client, connections = wire(monkeypatch, tmp_path)
    with pytest.raises(DataAdapterError, match='unsupported'):
        client._request(method, path, {}, context=RequestContext())
    assert not connections


@pytest.mark.parametrize('status,code', [(301,'blocked_url'),(307,'blocked_url'),
    (401,'authentication_failed'),(403,'authentication_failed'),(429,'rate_limit')])
def test_status_and_no_redirect(monkeypatch, tmp_path, status, code):
    client, connections = wire(monkeypatch, tmp_path, Reply(status, raw=b'not JSON', headers={'Location':'https://example.invalid'}))
    with pytest.raises(DataAdapterError, match=code): client.check_login(context=RequestContext())
    assert len(connections) == 1 and connections[0].closed


@pytest.mark.parametrize('message,code', [('context deadline exceeded','timeout'), ('captcha 验证','web_content_challenge'),
    ('not logged in','xhs_login_required'), ('笔记不存在','not_found'), ('unknown','xhs_backend_error')])
def test_raw_backend_errors_never_escape(monkeypatch, tmp_path, message, code):
    client, _ = wire(monkeypatch, tmp_path, Reply(500, {'success':False, 'details':message+' '+TOKEN}))
    with pytest.raises(DataAdapterError) as exc: client.check_login(context=RequestContext())
    assert exc.value.code == code and TOKEN not in str(exc.value)


@pytest.mark.parametrize('reply,code', [
    (Reply(raw=b'[]'), 'upstream_schema'),
    (Reply(raw=b'not JSON'), 'upstream_schema'),
    (Reply(data={'success':True,'data':[]}), 'upstream_schema'),
    (Reply(data={'success':True,'data':{'is_logged_in':'true'}}), 'upstream_schema'),
    (Reply(headers={'Content-Type':'text/html'}), 'upstream_schema'),
    (Reply(headers={'Content-Encoding':'gzip'}), 'upstream_schema'),
    (Reply(headers={'Content-Length':'invalid'}), 'upstream_schema'),
    (Reply(headers={'Content-Length':str(MAX_BYTES+1)}), 'response_too_large'),
    (Reply(raw=b' '* (MAX_BYTES+1)), 'response_too_large'),
])
def test_schema_and_size_bounds(monkeypatch, tmp_path, reply, code):
    client, connections = wire(monkeypatch, tmp_path, reply)
    with pytest.raises(DataAdapterError, match=code): client.check_login(context=RequestContext())
    assert connections[0].closed


@pytest.mark.parametrize('error,code', [(socket.timeout(TOKEN),'timeout'), (OSError(TOKEN),'xhs_backend_unavailable')])
def test_network_failure_is_safe(monkeypatch, tmp_path, error, code):
    client, connections = wire(monkeypatch, tmp_path, error=error)
    with pytest.raises(DataAdapterError) as exc: client.check_login(context=RequestContext())
    assert exc.value.code == code and TOKEN not in str(exc.value) and connections[0].closed


def test_cancellation_prevents_connect(monkeypatch, tmp_path):
    client, connections = wire(monkeypatch, tmp_path)
    ctx=RequestContext();ctx.cancel()
    with pytest.raises(RequestStopped, match='cancelled'): client.check_login(context=ctx)
    assert not connections


def test_insecure_cache_and_symlink_rejected(monkeypatch, tmp_path):
    client, connections = wire(monkeypatch, tmp_path)
    client.cache.root.parent.mkdir(mode=0o755)
    client.cache.root.parent.chmod(0o755)
    with pytest.raises(DataAdapterError, match='xhs_cache_unavailable'): client.check_login(context=RequestContext())
    client.cache.root.parent.rmdir()
    target=tmp_path/'target';target.mkdir(mode=0o700)
    client.cache.root.parent.symlink_to(target, target_is_directory=True)
    with pytest.raises(DataAdapterError, match='xhs_cache_unavailable'): client.check_login(context=RequestContext())
    assert not connections
