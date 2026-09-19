"""Private native-browser reader of one public Xueqiu post; no login actions."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
from threading import Thread
from urllib.parse import urljoin

from ir_search.context import RequestContext, RequestStopped
from ir_search.registry import DataAdapterError
from .platform_documents import _platform_url
from .public_web import _url
from ._xueqiu_tunnel import HOSTS, _Gate, _server


def _allow(url, *, target, method, kind, main):
    try: parsed, host, _ = _url(url)
    except DataAdapterError: return False
    if parsed.scheme != 'https' or host not in HOSTS or method != 'GET': return False
    if main:
        try: return _platform_url(url)[:2] == ('xueqiu', target)
        except DataAdapterError: return False
    # Article HTML and scripts suffice; exclude feeds, comments, analytics,
    # media, frames, forms and account API calls. Include the site's own scripts:
    # a normal initial page needs them before it can render the article.
    return kind in {'script', 'stylesheet'}


def _raise_failure(code):
    if code in {'cancelled', 'deadline_exceeded', 'operation_budget_exhausted'}:
        raise RequestStopped(code)
    raise DataAdapterError(code)


@dataclass
class _NavigationGuard:
    count: int = 0
    status: int = 0

    def start(self):
        self.count += 1
        if self.count > 4: raise DataAdapterError('web_content_challenge')

    def response(self, status):
        self.status = status
        code = {401: 'authentication_failed', 403: 'web_content_challenge',
                404: 'not_found', 410: 'not_found', 412: 'web_content_challenge', 429: 'rate_limit'}.get(status)
        if code: raise DataAdapterError(code)
        if status >= 500: raise DataAdapterError('network')


def _cookie_rows(cookie):
    if not cookie: return []
    rows = []
    for part in cookie.split(';'):
        name, sep, value = part.strip().partition('=')
        if not sep or not re.fullmatch(r'[!#$%&\'*+.^_`|~A-Za-z0-9-]+', name):
            raise DataAdapterError('source_config_error')
        rows.append({'name': name, 'value': value, 'url': 'https://xueqiu.com', 'secure': True,
                     'httpOnly': name in {'xq_a_token', 'xq_r_token', 'xq_id_token', 'xqat'}, 'sameSite': 'Lax'})
    return rows


async def _read(payload, context, gate, proxy):
    from playwright.async_api import async_playwright
    blocked, failure, navigation = 0, [], _NavigationGuard()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel=payload.get('channel', 'chromium'), headless=payload.get('headless', True), chromium_sandbox=True,
            proxy={'server': proxy, 'bypass': '<-loopback>', 'username': 'ir-search', 'password': gate.password},
            args=['--disable-background-networking', '--disable-component-update', '--disable-quic',
                  '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
                  '--disable-features=MediaRouter,OptimizationHints', '--js-flags=--max-old-space-size=512'])
        try:
            session = await browser.new_context(ignore_https_errors=False, accept_downloads=False,
                service_workers='block', viewport={'width':1280, 'height':900})
            if payload.get('cookie'): await session.add_cookies(_cookie_rows(payload['cookie']))
            page = await session.new_page()
            def response_received(response):
                request = response.request
                if request.is_navigation_request() and request.frame == page.main_frame:
                    try: navigation.response(response.status)
                    except DataAdapterError as exc: failure.append(exc.code)
            page.on('response', response_received)
            async def route_request(route):
                nonlocal blocked
                request = route.request
                main = request.is_navigation_request() and request.frame == page.main_frame
                try:
                    if failure:
                        blocked += 1
                        await route.abort()
                        return
                    if not _allow(request.url, target=payload['url'], method=request.method,
                                  kind=request.resource_type, main=main):
                        blocked += 1
                        if main: failure.append('blocked_url')
                        await route.abort()
                        return
                    if main:
                        navigation.start()
                    # Count every allowed HTTP request, including navigation retries.
                    context.begin_operation()
                    await route.continue_()
                except (DataAdapterError, RequestStopped) as exc:
                    failure.append(exc.code)
                    await route.abort()
            await session.route('**/*', route_request)
            await session.route_web_socket('**/*', lambda ws: ws.close())
            session.on('page', lambda other: asyncio.create_task(other.close()) if other != page else None)
            page.set_default_timeout(max(1, int(context.remaining_seconds()*1000)))
            try:
                await page.goto(payload['url'], wait_until='domcontentloaded')
                await page.locator('div.article__bd__detail').wait_for(state='visible',
                    timeout=max(1, int(min(15, context.remaining_seconds()-1)*1000)))
            except Exception:
                if failure: _raise_failure(failure[0])
                if gate.failure: _raise_failure(gate.failure)
                # The page is only used to classify failure; never returned as body.
                html = await page.content()
                if re.search(r'_waf_|g-recaptcha|人机验证|访问验证|请输入验证码', html, re.I):
                    raise DataAdapterError('web_content_challenge') from None
                raise DataAdapterError('no_extracted_text') from None
            context.check_active()
            if failure: _raise_failure(failure[0])
            if gate.failure: _raise_failure(gate.failure)
            if navigation.status != 200: raise DataAdapterError('upstream_schema')
            if _platform_url(page.url)[:2] != ('xueqiu', payload['url']): raise DataAdapterError('blocked_url')
            body, title = page.locator('div.article__bd__detail'), page.locator('h1.article__bd__title')
            if await body.count() != 1 or await title.count() != 1: raise DataAdapterError('upstream_schema')
            # No runtime JS/global state or broad page text is used as evidence.
            text = await body.inner_text()
            if not text.strip(): raise DataAdapterError('no_extracted_text')
            if len(text.encode()) > 2*1024*1024: raise DataAdapterError('response_too_large')
            name, time = page.locator('.article__author .name'), page.locator('.article__author a.time')
            publisher = await name.inner_text() if await name.count() == 1 else ''
            publication, publication_url = '', ''
            if await time.count() == 1:
                publication = await time.inner_text()
                publication_url = urljoin(page.url, await time.get_attribute('href') or '')
            return {'url': payload['url'], 'title': await title.inner_text(), 'text': text[:payload['max_chars']],
                'truncated': len(text)>payload['max_chars'], 'publisher': publisher,
                'publication': publication, 'publication_url': publication_url,
                'browser': {'backend': 'playwright_chromium', 'network': 'public_dns_pinned_connect',
                    'tls_verified': True, 'blocked_requests': blocked, 'wire_bytes': gate.wire_bytes,
                    'cookie_mode': 'explicit_env' if payload.get('cookie') else 'anonymous',
                    'login_performed': False, 'comments_included': False}}
        finally:
            try: await browser.close()
            except Exception: pass


def main():
    payload = json.loads(sys.stdin.read(65536))
    context = RequestContext(timeout_seconds=payload['timeout'], max_operations=payload['operations'])
    gate = _Gate(context)
    server = _server(gate)
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        result = asyncio.run(_read(payload, context, gate, 'http://127.0.0.1:'+str(server.server_port)))
    except (DataAdapterError, RequestStopped) as exc: result = {'error': gate.failure or exc.code}
    except Exception as exc:
        message = str(exc).lower()
        result = {'error': 'browser_unavailable' if "executable doesn't exist" in message
                  else 'timeout' if 'timeout' in message else 'browser_failed'}
    finally:
        context.cancel(); gate.close(); server.shutdown(); server.server_close(); thread.join(timeout=1)
    result['operations'] = context.operations
    Path(payload['output']).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__': main()
