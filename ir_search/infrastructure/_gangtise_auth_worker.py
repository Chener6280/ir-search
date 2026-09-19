"""Disposable optional browser login; secrets only through private stdin/state."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

from .gangtise import HOST, _error_code
from ir_search.registry import DataAdapterError


def main():
    """Use the ordinary login UI; new-device/SMS/QR challenges require the user."""
    os.umask(0o077)
    result = {'error': 'browser_failed'}; output = None; stage = 'launch'
    try:
        from playwright.sync_api import sync_playwright
        raw = sys.stdin.buffer.read(32769)
        if len(raw) > 32768: raise DataAdapterError('unsupported')
        config = json.loads(raw); output = Path(config['output'])
        limit = time.monotonic()+min(300, float(config['timeout']))
        with sync_playwright() as pw:
            browser = pw.chromium.launch_persistent_context(config['profile_dir'],
                executable_path=config['executable'] or None, headless=not config['interactive'],
                accept_downloads=False, service_workers='block', viewport={'width':1280, 'height':900},
                timeout=min(45000, max(1, int((limit-time.monotonic())*1000))))
            page = browser.pages[0] if browser.pages else browser.new_page()
            page.set_default_timeout(15000)
            count = 0; login_error = None; access_token = None
            def gate(route):
                nonlocal count
                count += 1; request = route.request; parsed = urlsplit(request.url)
                host = parsed.hostname or ''
                allowed = (parsed.scheme == 'https' and parsed.port in (None,443) and not parsed.username
                           and not parsed.password and count <= 300)
                # Static official assets and normal human-completed WeChat authentication.
                allowed = allowed and (host == HOST or (config['interactive'] and
                    (host == 'open.weixin.qq.com' or host == 'res.wx.qq.com' or host.endswith('.wx.qq.com'))))
                # Block vendor telemetry, generation, account writes and unrelated background calls.
                path = parsed.path
                if host == HOST and path.startswith('/application/'):
                    allowed = allowed and (path.startswith('/application/auth/oauth/') or
                        path == '/application/auth/authority/getUserMenuTree/v2' or
                        path.startswith('/application/wechat/wx-server/external/'))
                if host != HOST and any(s in request.url+(request.post_data or '') for s in (config['phone'], config['password'])):
                    allowed = False
                route.continue_() if allowed else route.abort()
            browser.route('**/*', gate)
            def response(reply):
                nonlocal login_error, access_token
                if urlsplit(reply.url).hostname != HOST: return
                path = urlsplit(reply.url).path
                if path not in {'/application/auth/oauth/two-stage/login',
                    '/application/auth/oauth/two-stage/changeProduct',
                    '/application/auth/oauth/two-stage/verify/code/login',
                    '/application/auth/oauth/two-stage/wechat/qrcode/login'}: return
                try:
                    body = reply.json()
                    if body.get('status') is not True: login_error = _error_code(reply.status, body)
                    elif path.endswith('/changeProduct'):
                        data = body.get('data') or {}; access_token = data.get('access_token')
                except Exception: pass
            page.on('response', response)
            stage = 'login'
            page.goto('https://'+HOST+'/#/login', wait_until='domcontentloaded', timeout=45000)
            field = page.get_by_placeholder('请输入您的手机号/账号')
            # A previously validated private browser may already have a product session.
            try: field.wait_for(timeout=5000)
            except Exception: pass
            if field.count() and field.is_visible():
                field.fill(config['phone']); page.get_by_placeholder('请输入密码').fill(config['password'])
                boxes = page.locator('input[type=checkbox]')
                if boxes.count() != 3: raise DataAdapterError('upstream_schema')
                for index in (0,1):
                    if boxes.nth(index).is_checked(): boxes.nth(index).evaluate('(e)=>e.click()')
                if not boxes.nth(2).is_checked(): boxes.nth(2).evaluate('(e)=>e.click()')
                page.get_by_text('登录', exact=True).click()
            while time.monotonic() < limit-1:
                if access_token: break
                # Token is read only after the app leaves login, not a preliminary account token.
                if not urlsplit(page.url).fragment.lower().startswith('/login'):
                    candidate = page.evaluate("localStorage.getItem('token')")
                    if candidate:
                        # Reused browser state is not proof that a token remains valid.
                        response_check = page.request.post('https://'+HOST+'/application/auth/oauth/surplus/expires',
                            data={'accessToken': candidate}, headers={'Authorization':'Bearer '+candidate},
                            max_redirects=0, timeout=min(15000, max(1,int((limit-time.monotonic())*1000))))
                        check = response_check.json()
                        if (response_check.status == 200 and check.get('status') is True
                                and type(check.get('data')) in (int,float) and check['data'] > 0):
                            access_token = candidate; break
                        page.evaluate("localStorage.removeItem('token')")
                        raise DataAdapterError('authentication_failed')
                if login_error and not config['interactive']: raise DataAdapterError(login_error)
                if login_error and login_error != 'gangtise_login_challenge': raise DataAdapterError(login_error)
                page.wait_for_timeout(250)
            if not access_token: raise DataAdapterError(login_error or 'gangtise_login_challenge')
            result = {'token': access_token}
            browser.close()
    except DataAdapterError as exc: result = {'error': exc.code}
    except ImportError: result = {'error': 'browser_dependency_missing'}
    except Exception: result = {'error': 'browser_unavailable' if stage == 'launch' else 'authentication_failed'}
    finally:
        if output is not None:
            try:
                with output.open('x', encoding='utf-8') as stream: json.dump(result, stream)
            except OSError: pass


if __name__ == '__main__': main()
