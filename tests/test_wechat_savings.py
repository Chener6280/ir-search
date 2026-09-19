"""Cost avoidance must preserve publication dates, coverage and exact citations."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

import pytest

from ir_search import MaterialRequest, RequestContext, retrieve
from ir_search.context import RequestStopped
from ir_search.infrastructure import wechat as wc
from ir_search.infrastructure.wechat_cache import _WechatCache
from ir_search.infrastructure.web_browser import RenderedPage
from ir_search.registry import DataAdapterError
from test_wechat_materials import URL, HTML, NOW, ACCOUNT, PROFILE, Client, doc, row, request
from ir_search.infrastructure.public_web import _Reply

LOADING = '<title>文章</title><div id="js_content" style="display:none">加载中</div><script src="app.js"></script>'


def transport(html=HTML, calls=None):
    def read(url, **kw):
        kw['context'].begin_operation()
        if calls is not None: calls.append(url)
        return _Reply(200, 'text/html', '', html.encode(), NOW)
    return read


def test_rendered_wechat_uses_article_container_not_generic_page_text(tmp_path):
    calls = []
    def render(url, **kw):
        calls.append(kw)
        return RenderedPage(url, HTML, NOW, {'blocked_requests': 2})
    d = wc.fetch_wechat_document(URL, context=RequestContext(), transport=transport(LOADING), renderer=render,
        client=Client(), cache=_WechatCache(tmp_path/'cache'))
    assert d.extra['text_provider'] == 'wechat_browser'
    assert d.extra['web_read']['vendor_body_calls'] == 0
    assert d.extra['web_read']['browser_attempted']
    assert '页面导航' not in d.text and '登录' not in d.text and 'bad()' not in d.text
    assert calls[0]['allowed_domains'] == ('mp.weixin.qq.com',)


@pytest.mark.parametrize('html', ['<title>环境异常</title>请完成验证<script></script>',
    '<title>captcha</title><script></script>', '<title>普通空页面</title>'])
def test_auto_skips_challenges_and_non_loading_pages(html, tmp_path):
    def forbidden(*a, **k): pytest.fail('no browser challenge automation')
    d = wc.fetch_wechat_document(URL, context=RequestContext(), transport=transport(html), renderer=forbidden,
        client=Client(), cache=_WechatCache(tmp_path/'cache'))
    assert d.extra['text_provider'] == 'dajiala'
    assert not d.extra['web_read']['browser_attempted']


@pytest.mark.parametrize('code', ['browser_dependency_missing', 'browser_version_unsupported', 'browser_failed', 'browser_robots_denied'])
def test_browser_failures_keep_vendor_available_and_visible(code, tmp_path):
    def render(*a, **k): raise DataAdapterError(code)
    d = wc.fetch_wechat_document(URL, context=RequestContext(), transport=transport(LOADING), renderer=render,
        client=Client(), cache=_WechatCache(tmp_path/'cache'))
    assert d.extra['text_provider'] == 'dajiala' and d.extra['web_read']['vendor_body_calls'] == 1
    assert {'reader':'crawl4ai', 'state':code} in d.extra['web_read']['attempts']


def test_browser_reservation_and_cancel(tmp_path):
    def exhausted(url, *, context, **kw):
        context.operations = context.max_operations
        raise RequestStopped('operation_budget_exhausted')
    ctx = RequestContext(max_operations=6)
    d = wc.fetch_wechat_document(URL, context=ctx, transport=transport(LOADING), renderer=exhausted,
        client=Client(), cache=_WechatCache(tmp_path/'cache'))
    assert ctx.operations == 6 and d.extra['text_provider'] == 'dajiala'
    ctx = RequestContext()
    def cancelled(*a, **kw):
        ctx.cancel()
        raise RequestStopped('cancelled')
    with pytest.raises(RequestStopped, match='cancelled'):
        wc.fetch_wechat_document(URL, context=ctx, transport=transport(LOADING), renderer=cancelled,
            client=Client(), cache_mode='off')


def test_body_cache_reuses_large_extraction_across_clients_limits_and_tracking(tmp_path, monkeypatch):
    cache = _WechatCache(tmp_path/'cache')
    calls = []
    first = wc.fetch_wechat_document(URL, context=RequestContext(), max_chars=3, cache=cache, transport=transport(calls=calls))
    second = wc.fetch_wechat_document(URL+'&scene=9', context=RequestContext(), max_chars=10000,
        cache=_WechatCache(cache.root), transport=lambda *a, **k: pytest.fail('no network for cache hit'))
    assert len(calls) == 1 and len(second.text) > len(first.text)
    assert second.fetched_at == first.fetched_at == NOW
    assert second.extra['web_read']['cache_state'] == 'hit'
    assert second.extra['web_read']['freshness'] == 'cached_snapshot_not_revalidated'
    assert second.extra['web_read']['vendor_body_calls'] == 0
    assert 'text_truncated' not in second.warnings
    monkeypatch.setattr(wc, '_default_cache', lambda *a: cache)
    bundle = retrieve(MaterialRequest('动销', (URL,)))
    m = bundle.materials[0]
    assert m.read_details['cache_state'] == 'hit'
    assert m.provenance.fetched_at == NOW
    for s in m.evidence_spans: assert s['text'] == m.text[s['start_char']:s['end_char']]


def test_refresh_off_expiry_and_failed_refresh_never_silently_serve_stale(tmp_path):
    clock = [1000.0]
    cache = _WechatCache(tmp_path/'cache', clock=lambda: clock[0])
    calls = []
    def read(mode='use', text=HTML):
        return wc.fetch_wechat_document(URL, context=RequestContext(), cache=cache, cache_mode=mode, transport=transport(text,calls))
    read(); read(); assert len(calls) == 1
    read('refresh'); assert len(calls) == 2
    read('off'); assert len(calls) == 3
    clock[0] += 86401
    read(); assert len(calls) == 4
    with pytest.raises(DataAdapterError): read('refresh', '<title>failed</title>')
    assert len(calls) == 5


def test_simultaneous_reads_coalesce_to_one_paid_body_call(tmp_path):
    calls = []
    class Vendor(Client):
        def article(self, *a, **kw):
            calls.append(1)
            time.sleep(.04)
            return super().article(*a, **kw)
    def read(_):
        return wc.fetch_wechat_document(URL, context=RequestContext(), client=Vendor(),
            cache=_WechatCache(tmp_path/'cache'), transport=transport('<title>blocked</title>'))
    with ThreadPoolExecutor(max_workers=4) as pool: results = list(pool.map(read, range(4)))
    assert len(calls) == 1
    assert sorted(d.extra['web_read']['vendor_body_calls'] for d in results) == [0,0,0,1]


def history_result(rows, *, end=0, cursor='tail'):
    return {'code':0, 'data':rows, 'nickname':ACCOUNT.name, 'ghid':ACCOUNT.ghid, 'is_end':end, 'offset':cursor,
        'remain_money':'MUST_NOT_PERSIST', 'key':'MUST_NOT_PERSIST'}


def make_client(responses, cache, calls, **kwargs):
    def send(url, **kw):
        calls.append(url.rsplit('/',1)[-1]); kw['context'].begin_operation()
        return _Reply(200,'application/json','',json.dumps(responses.pop(0)).encode(),NOW)
    return wc.DajialaMaterialClient(PROFILE, transport=send, cache=cache, **kwargs)


def test_head_refresh_reuses_unchanged_tail_and_invalidates_it_on_new_posts(tmp_path):
    clock = [1000.0]; calls = []
    cache = _WechatCache(tmp_path/'cache', clock=lambda: clock[0])
    responses = [history_result([row()]), history_result([row(124)],end=1)]
    c = make_client(responses, cache, calls)
    c.history(ACCOUNT, context=RequestContext()); c.history(ACCOUNT, cursor='tail', context=RequestContext())
    assert len(calls) == 2
    # A newly constructed registry reuses both snapshots within the head TTL.
    c = make_client([], cache, calls)
    assert c.history(ACCOUNT, context=RequestContext()).cache_state == 'hit'
    assert c.history(ACCOUNT,cursor='tail',context=RequestContext()).cache_state == 'hit'
    clock[0] += 301
    c = make_client([history_result([row()])], cache, calls)
    assert c.history(ACCOUNT, context=RequestContext()).cache_state == 'fresh'
    assert c.history(ACCOUNT, cursor='tail', context=RequestContext()).cache_state == 'hit'
    assert len(calls) == 3
    clock[0] += 301
    c = make_client([history_result([row(125)]),history_result([row()],end=1)],cache,calls)
    c.history(ACCOUNT, context=RequestContext())
    assert c.history(ACCOUNT,cursor='tail',context=RequestContext()).cache_state == 'fresh'
    assert len(calls) == 5
    assert all('MUST_NOT_PERSIST' not in p.read_text() for p in cache.root.glob('*.json'))


def test_persisted_account_resolution_expires_and_refresh_overrides_it(tmp_path):
    clock = [1000.]; calls = []; cache = _WechatCache(tmp_path/'cache', clock=lambda:clock[0])
    account = replace(ACCOUNT, ghid='')
    response = {'code':0,'data':[{'name':account.name,'ghid':ACCOUNT.ghid}]}
    assert make_client([response],cache,calls).resolve_account(account,context=RequestContext()) == ACCOUNT.ghid
    assert make_client([],cache,calls).resolve_account(account,context=RequestContext()) == ACCOUNT.ghid
    assert len(calls) == 1
    make_client([response],cache,calls,cache_mode='refresh').resolve_account(account,context=RequestContext())
    clock[0] += 7*86400+1
    make_client([response],cache,calls).resolve_account(account,context=RequestContext())
    assert len(calls) == 3


def test_cache_private_permissions_corruption_failure_and_bounds(tmp_path):
    cache = _WechatCache(tmp_path/'cache')
    with cache._guard(RequestContext()) as enabled:
        assert enabled
        cache._put('article',URL, {'ok':True})
        assert cache._get('article',URL,60) == {'ok':True}
        cache._path('article',URL).write_text('{bad-json')
        assert cache._get('article',URL,60) is None
        assert cache.warning == 'wechat_cache_invalid'
    if os.name == 'posix':
        assert cache.root.stat().st_mode & 0o777 == 0o700
        assert cache._path('article',URL).stat().st_mode & 0o777 == 0o600
        cache.root.chmod(0o755)
        d = wc.fetch_wechat_document(URL, context=RequestContext(), cache=cache, transport=transport())
        assert 'wechat_cache_unavailable' in d.warnings
    target = tmp_path/'target'; target.mkdir()
    symlink = tmp_path/'linked'; symlink.symlink_to(target, target_is_directory=True)
    with _WechatCache(symlink)._guard(RequestContext()) as enabled: assert not enabled


def test_waiting_cache_lock_respects_deadline_and_releases_fd(tmp_path):
    cache = _WechatCache(tmp_path/'cache')
    with cache._guard(RequestContext()):
        with pytest.raises(RequestStopped, match='deadline_exceeded'):
            with cache._guard(RequestContext(timeout_seconds=.04)): pytest.fail('must not acquire')
    with cache._guard(RequestContext()) as enabled: assert enabled


@pytest.mark.parametrize('mode',[None,'invalid',True])
def test_public_cache_options_are_validated(mode):
    with pytest.raises(ValueError): MaterialRequest('q',(URL,),wechat_cache_mode=mode)
    with pytest.raises(ValueError): request(wechat_cache_mode=mode)
    with pytest.raises(ValueError): wc.fetch_wechat_document(URL,context=RequestContext(),cache_mode=mode)


def test_http_mode_does_not_launch_browser(tmp_path):
    d = wc.fetch_wechat_document(URL, context=RequestContext(), transport=transport(LOADING), mode='http',
        client=Client(), cache=_WechatCache(tmp_path/'cache'), renderer=lambda *a, **k:pytest.fail('browser disabled'))
    assert not d.extra['web_read']['browser_attempted']


def test_bad_snapshot_cannot_override_article_identity_or_text_hash(tmp_path):
    cache = _WechatCache(tmp_path/'cache')
    for field, value in [('url', URL.replace('mid=123', 'mid=999')), ('text_hash','wrong')]:
        data = doc().to_dict(); data[field] = value
        with cache._guard(RequestContext()): cache._put(wc._ARTICLE_CACHE,URL,data)
        calls = []
        d = wc.fetch_wechat_document(URL,context=RequestContext(),cache=cache,transport=transport(calls=calls))
        assert len(calls) == 1 and d.url == URL
        assert 'wechat_cache_invalid' in d.warnings


def test_cached_tail_without_matching_head_is_never_reused(tmp_path):
    calls = []; cache = _WechatCache(tmp_path/'cache')
    c = make_client([history_result([row()]),history_result([row(124)],end=1)],cache,calls)
    c.history(ACCOUNT,context=RequestContext());c.history(ACCOUNT,cursor='tail',context=RequestContext())
    c = make_client([history_result([row(125)],end=1)],cache,calls)
    assert c.history(ACCOUNT,cursor='tail',context=RequestContext()).cache_state == 'fresh'
    assert len(calls) == 3


def test_cache_evicts_oldest_entries_and_never_stores_off_reads(tmp_path):
    cache = _WechatCache(tmp_path/'cache')
    d = wc.fetch_wechat_document(URL,context=RequestContext(),cache=cache,cache_mode='off',transport=transport())
    assert not cache.root.exists() and d.extra['web_read']['cache_state'] == 'off'
    with cache._guard(RequestContext()):
        for n in range(513): cache._put('item',str(n),n)
        assert len(list(cache.root.glob('*.json'))) == 512
        assert cache._get('item','0',86400) is None
        assert cache._get('item','512',86400) == 512


def test_mcp_propagates_wechat_read_and_cache_modes(monkeypatch):
    from ir_search import mcp_server
    options = []
    def read(url, **kw):
        options.append(kw)
        return doc(url)
    monkeypatch.setattr(wc,'fetch_wechat_document',read)
    payload = mcp_server.retrieve_payload('动销',[URL],wechat_cache_mode='refresh',web_read_mode='http')
    assert payload['materials'] and options[0]['cache_mode'] == 'refresh' and options[0]['mode'] == 'http'
