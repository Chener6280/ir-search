"""Synthetic isolation, budget and provenance tests for the optional browser reader."""
import base64
import json
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest

from ir_search import RequestContext, MaterialRequest, retrieve
from ir_search.context import RequestStopped
from ir_search.infrastructure import xueqiu_browser as reader
from ir_search.infrastructure import _xueqiu_browser_worker as worker
from ir_search.infrastructure import _xueqiu_tunnel as tunnel
from ir_search.infrastructure.credentials import source_configuration_status, SourceConfigError
from ir_search.infrastructure.public_web import _Reply
from ir_search.registry import DataAdapterError
from ir_search.services import source_diagnostics

URL = 'https://xueqiu.com/123/456'


def snapshot(**changes):
    return {'url': URL, 'title': '渠道\n调研', 'text': '来源：公司原文\n茅台动销正文。',
        'publisher': '测试作者', 'publication': '发布于2026-09-18 09:30', 'publication_url': URL,
        'truncated': False, 'browser': {'network':'public_dns_pinned_connect', 'tls_verified':True,
            'login_performed':False, 'blocked_requests':5, 'wire_bytes':1024}, **changes}


def fixture(monkeypatch, data=None, *, cookie=False):
    profile = reader.XueqiuReadProfile('browser', cookie)
    monkeypatch.setattr(reader, 'xueqiu_read_profile', lambda **k: profile)
    monkeypatch.setattr(reader, '_dependency', lambda: None)
    monkeypatch.setattr(reader, '_cookie', lambda _: 'xq_a_token=synthetic-private-value')
    calls = []
    def run(url, **kw):
        calls.append((url, kw))
        return snapshot() if data is None else data
    monkeypatch.setattr(reader, '_run', run)
    return calls


def test_profile_defaults_and_explicit_browser_selection():
    assert reader.xueqiu_read_profile(values={}) == reader.XueqiuReadProfile()
    assert reader.xueqiu_read_profile(values={'XUEQIU_READ_MODE':'browser'}).browser_use_cookie is False
    assert reader.xueqiu_read_profile(values={'XUEQIU_BROWSER_USE_COOKIE':'true'}).browser_use_cookie
    custom = reader.xueqiu_read_profile(values={'XUEQIU_READ_MODE':'browser',
        'XUEQIU_BROWSER_HEADLESS':'false', 'XUEQIU_BROWSER_CHANNEL':'chrome'})
    assert not custom.browser_headless and custom.browser_channel == 'chrome'
    for values in ({'XUEQIU_READ_MODE':'stealth'}, {'XUEQIU_BROWSER_USE_COOKIE':'yes'},
                   {'XUEQIU_BROWSER_HEADLESS':'yes'}, {'XUEQIU_BROWSER_CHANNEL':'/some/executable'}):
        with pytest.raises(SourceConfigError): reader.xueqiu_read_profile(values=values)


def test_browser_article_has_publication_author_source_and_precise_citations(monkeypatch):
    calls = fixture(monkeypatch)
    result = retrieve(MaterialRequest('茅台动销', [URL]), context=RequestContext()).to_dict()
    mat = result['materials'][0]
    assert calls[0][1]['cookie'] == ''
    assert mat['title'] == '渠道调研' and mat['text'].startswith('来源：公司原文')
    assert mat['published_at'] == '2026-09-18T09:30:00+08:00'
    assert mat['provenance']['provider'] == 'xueqiu' and mat['provenance']['authority'] == 'ugc'
    assert mat['provenance']['evidence_type'] == 'opinion'
    assert mat['extraction_method'] == 'xueqiu_browser_visible_article'
    assert mat['evidence_spans']
    assert all(s['text'] == mat['text'][s['start_char']:s['end_char']] for s in mat['evidence_spans'])
    assert mat['read_details']['browser']['cookie_mode'] == 'anonymous'
    assert mat['read_details']['browser']['experimental'] is True
    assert calls[0][1]['headless'] is True and calls[0][1]['channel'] == 'chromium'
    assert 'synthetic-private-value' not in json.dumps(result)


def test_explicit_cookie_mode_is_scoped_and_not_returned(monkeypatch):
    calls = fixture(monkeypatch, cookie=True)
    d = reader.read_xueqiu_browser_document(URL, context=RequestContext())
    assert calls[0][1]['cookie'] == 'xq_a_token=synthetic-private-value'
    assert d.extra['web_read']['browser']['cookie_mode'] == 'explicit_env'
    assert 'synthetic-private-value' not in json.dumps(d.extra)
    monkeypatch.setattr(reader, '_cookie', lambda _: '')
    with pytest.raises(DataAdapterError, match='no_credential'):
        reader.read_xueqiu_browser_document(URL, context=RequestContext())


@pytest.mark.parametrize('changes,code', [
    ({'url':'https://xueqiu.com/123/789'}, 'blocked_url'),
    ({'text':''}, 'no_extracted_text'), ({'text':[]}, 'upstream_schema'),
    ({'truncated':'no'}, 'upstream_schema'), ({'browser':{'tls_verified':False}}, 'upstream_schema'),
    ({'publisher': 'x'*501}, 'upstream_schema'),
    ({'text':'reflected synthetic-private-value'}, 'upstream_schema'),
    ({'publisher':'reflected synthetic-private-value'}, 'upstream_schema'),
])
def test_malformed_or_reflected_snapshot_is_not_evidence(monkeypatch, changes, code):
    fixture(monkeypatch, snapshot(**changes), cookie=True)
    with pytest.raises(DataAdapterError, match=code): reader.read_xueqiu_browser_document(URL, context=RequestContext())


def test_updated_date_is_not_fabricated_publication(monkeypatch):
    fixture(monkeypatch, snapshot(publication='修改于2026-09-18 09:30', truncated=True))
    d = reader.read_xueqiu_browser_document(URL, context=RequestContext())
    assert d.published_at is None and 'published_date_unknown' in d.warnings and 'text_truncated' in d.warnings


@pytest.mark.parametrize('publication_url', ['', 'https://evil.test/123/456', 'https://xueqiu.com/123/789'])
def test_missing_or_unbound_time_link_leaves_date_unknown(monkeypatch, publication_url):
    fixture(monkeypatch, snapshot(publication_url=publication_url))
    d = reader.read_xueqiu_browser_document(URL, context=RequestContext())
    assert d.published_at is None and d.text and 'published_date_unknown' in d.warnings


def test_cancelled_or_wrong_platform_does_not_start_browser(monkeypatch):
    monkeypatch.setattr(reader, '_dependency', lambda: pytest.fail('dependency discovery'))
    c = RequestContext(); c.cancel()
    with pytest.raises(RequestStopped, match='cancelled'): reader.read_xueqiu_browser_document(URL, context=c)
    with pytest.raises(DataAdapterError, match='unsupported'):
        reader.read_xueqiu_browser_document('https://www.bilibili.com/video/BV17x411w7KC', context=RequestContext())
    with pytest.raises(ValueError): reader.read_xueqiu_browser_document(URL, context=RequestContext(), max_chars=True)


def test_missing_or_unsupported_dependency_has_typed_diagnostic(monkeypatch):
    def absent(_): raise reader.PackageNotFoundError()
    monkeypatch.setattr(reader, 'version', absent)
    with pytest.raises(DataAdapterError, match='browser_dependency_missing'): reader._dependency()
    for old in ('1.45.0', '1.48.0'):
        monkeypatch.setattr(reader, 'version', lambda _, old=old: old)
        with pytest.raises(DataAdapterError, match='browser_version_unsupported'): reader._dependency()
    monkeypatch.setattr(reader, 'version', lambda _: '1.63.0')
    reader._dependency()


@pytest.mark.parametrize('url,method,kind,main,allowed', [
    (URL,'GET','document',True,True), (URL+'?utm_source=x','GET','document',True,True),
    ('https://xueqiu.com/123/789','GET','document',True,False),
    ('https://xueqiu.com/login','GET','document',True,False),
    (URL,'POST','document',True,False), ('http://xueqiu.com/123/456','GET','document',True,False),
    ('https://127.0.0.1/','GET','script',False,False),
    ('https://assets.imedao.com/public.js','GET','script',False,True),
    ('https://assets.imedao.com/a.css','GET','stylesheet',False,True),
    ('https://g.alicdn.com/a.js','GET','script',False,True),
    ('https://xueqiu.com/site-loader.js','GET','script',False,True),
    ('https://assets.imedao.com.attacker.test/a.js','GET','script',False,False),
    ('https://assets.imedao.com/a?token=secret','GET','script',False,False),
    ('https://xueqiu.com/statuses/comments.json?id=456','GET','xhr',False,False),
    ('https://assets.imedao.com/a','GET','document',False,False),
    ('https://assets.imedao.com/a.mp4','GET','media',False,False),
    ('https://assets.imedao.com/a','POST','script',False,False),
])
def test_browser_request_boundary(url, method, kind, main, allowed):
    assert worker._allow(url, target=URL, method=method, kind=kind, main=main) is allowed


def test_optional_cookies_never_use_wide_domain_and_auth_is_httponly():
    assert worker._cookie_rows('') == []
    rows = worker._cookie_rows('xq_a_token=private-value; theme=light')
    assert all(r['url']=='https://xueqiu.com' and r['secure'] for r in rows)
    assert rows[0]['httpOnly'] and not rows[1]['httpOnly']
    with pytest.raises(DataAdapterError): worker._cookie_rows('bad name=value')


@pytest.mark.parametrize('code', ['cancelled', 'deadline_exceeded', 'operation_budget_exhausted'])
def test_stop_reason_is_preserved_instead_of_invalid_adapter_error(code):
    with pytest.raises(RequestStopped, match=code): worker._raise_failure(code)


def test_refresh_loop_stops_before_fifth_navigation():
    guard = worker._NavigationGuard()
    for _ in range(4): guard.start()
    with pytest.raises(DataAdapterError, match='web_content_challenge'): guard.start()
    guard.response(200)
    assert guard.status == 200


@pytest.mark.parametrize('status,code', [(401,'authentication_failed'), (403,'web_content_challenge'),
    (404,'not_found'), (410,'not_found'), (412,'web_content_challenge'), (429,'rate_limit'), (503,'network')])
def test_bad_navigation_response_cannot_be_used_as_article(status,code):
    with pytest.raises(DataAdapterError, match=code): worker._NavigationGuard().response(status)


def test_tunnel_has_per_worker_auth_and_fixed_authorities(monkeypatch):
    gate = tunnel._Gate(RequestContext())
    auth = 'Basic '+base64.b64encode(('ir-search:'+gate.password).encode()).decode()
    assert gate.authenticate(auth) and not gate.authenticate('Basic wrong') and not gate.authenticate('坏')
    assert gate.password not in repr(gate)
    monkeypatch.setattr(tunnel, '_resolve', lambda *a: pytest.fail('unexpected DNS'))
    for authority in ('127.0.0.1:443','xueqiu.com:80','xueqiu.com.attacker.test:443','xueqiu.com:443@evil.test'):
        with pytest.raises(DataAdapterError, match='blocked_url'): gate.connect(authority)


def test_tunnel_pins_public_address_and_does_not_resolve_twice(monkeypatch):
    address=(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))
    calls=[]
    class Sock:
        def settimeout(self, value): assert value > 0
        def connect(self, value): calls.append(value)
        def shutdown(self, value): pass
        def close(self): calls.append('closed')
    monkeypatch.setattr(tunnel, '_resolve', lambda *a: address)
    monkeypatch.setattr(socket, 'socket', lambda *a: Sock())
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: pytest.fail('second resolution'))
    gate=tunnel._Gate(RequestContext()); gate.connect('xueqiu.com:443'); gate.close()
    assert calls == [address[4], 'closed']


def test_tunnel_rejects_private_dns_and_enforces_wire_budget(monkeypatch):
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))])
    gate=tunnel._Gate(RequestContext(),byte_limit=8)
    with pytest.raises(DataAdapterError,match='blocked_url'): gate.connect('xueqiu.com:443')
    gate.charge(8)
    with pytest.raises(DataAdapterError,match='browser_budget_exhausted'): gate.charge(1)
    assert gate.wire_bytes==8


def test_worker_boundary_sanitizes_environment_and_accounts_child(monkeypatch):
    seen={}
    class Input:
        def write(self, raw):
            payload=json.loads(raw);seen['payload']=payload
            Path(payload['output']).write_text(json.dumps({'operations':2,**snapshot()}))
        def close(self): pass
    process=SimpleNamespace(stdin=Input(),poll=lambda:0)
    def popen(args,**kw): seen.update(args=args,**kw);return process
    monkeypatch.setenv('SOME_PRIVATE_API_KEY','private-environment-secret')
    monkeypatch.setattr(reader.subprocess,'Popen',popen)
    monkeypatch.setattr(reader,'_stop',lambda p:seen.update(stopped=True))
    c=RequestContext(max_operations=5)
    result=reader._run(URL,context=c,max_chars=100,cookie='synthetic-cookie')
    assert c.operations==3 and result['text']
    assert seen['payload']['cookie']=='synthetic-cookie' and seen['payload']['operations']==4
    assert 'SOME_PRIVATE_API_KEY' not in seen['env']
    assert '-I' in seen['args'] and 'synthetic-cookie' not in repr(seen['args'])
    assert seen['stopped'] and not Path(seen['cwd']).exists()


def test_source_health_reports_backend_without_claiming_runtime(tmp_path, monkeypatch):
    p=tmp_path/'sources.env';p.write_text('XUEQIU_MATERIALS_ENABLED=true\nBOCHA_API_KEY=synthetic-key\nXUEQIU_READ_MODE=browser\n');p.chmod(0o600)
    status=source_configuration_status(env_file=p)
    row=next(s for s in status['sources'] if s['provider']=='xueqiu')
    assert row['read_mode']=='browser' and row['browser_cookie_mode']=='anonymous'
    assert not row['browser_runtime_verified'] and not row['live_verified']
    assert row['browser_experimental'] and row['browser_channel']=='chromium' and row['browser_headless']
    monkeypatch.setattr(source_diagnostics,'find_spec',lambda _:None)
    d=source_diagnostics.diagnose_sources(['xueqiu'],env_file=p)['sources'][0]
    assert d['state']=='dependency_missing' and not d['retrieve_live_verified']
    assert d['reader_configuration']['read_mode']=='browser' and d['reader_configuration']['browser_experimental']


def test_worker_crash_without_usage_report_reserves_budget_and_cleans_up(monkeypatch):
    class Input:
        def write(self, raw): pass
        def close(self): pass
    process=SimpleNamespace(stdin=Input(),poll=lambda:1)
    seen={}
    monkeypatch.setattr(reader.subprocess,'Popen',lambda *a,**kw:process)
    monkeypatch.setattr(reader,'_stop',lambda p:seen.update(stopped=True))
    c=RequestContext(max_operations=5)
    with pytest.raises(DataAdapterError,match='browser_failed'):
        reader._run(URL,context=c,max_chars=100,cookie='')
    assert c.operations==5 and seen['stopped']


def test_cancelled_child_is_terminated_without_returning_late_data(monkeypatch):
    c=RequestContext(max_operations=5)
    class Input:
        def write(self, raw): c.cancel()
        def close(self): pass
    process=SimpleNamespace(stdin=Input(),poll=lambda:None)
    stopped=[]
    monkeypatch.setattr(reader.subprocess,'Popen',lambda *a,**kw:process)
    monkeypatch.setattr(reader,'_stop',lambda p:stopped.append(p))
    with pytest.raises(RequestStopped,match='cancelled'):
        reader._run(URL,context=c,max_chars=100,cookie='')
    assert stopped==[process] and c.operations==5
