"""Disposable, bounded Playwright login worker. Private stdin; safe JSON stdout."""
from __future__ import annotations

import json
import os
import sys
import time
from urllib.parse import urlsplit

from .alphapai import HOST, LIST_PATH, DETAIL_PATH, MAX_BYTES, AlphapaiProfile, _validate, _payload, _checked_reply
from ir_search.registry import DataAdapterError

_FETCH = """async ({path,payload,token,device,timeout,maxBytes}) => {
 const ctl=new AbortController(), timer=setTimeout(()=>ctl.abort(),timeout);
 try {
  const r=await fetch(path,{method:payload===null?'GET':'POST',credentials:'include',redirect:'error',signal:ctl.signal,
   headers:{'content-type':'application/json','x-from':'web','Authorization':token,'X-device':device},
   body:payload===null?undefined:JSON.stringify(payload)});
  const length=r.headers.get('content-length');if(length && Number(length)>maxBytes) {ctl.abort();return {error:'response_too_large'}};
  const reader=r.body.getReader(), chunks=[];let size=0;
  while(true){const {done,value}=await reader.read();if(done)break;size+=value.length;
   if(size>maxBytes){ctl.abort();return {error:'response_too_large'}};chunks.push(value)}
  const all=new Uint8Array(size);let offset=0;for(const c of chunks){all.set(c,offset);offset+=c.length}
  let body;try{body=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(all))}catch{return {http:r.status,body:null}};
  return {http:r.status,body};
 } finally {clearTimeout(timer)}
}"""


def _emit(value):
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if len(raw.encode()) > MAX_BYTES: raw = '{"error":"response_too_large"}'
    sys.stdout.write(raw+'\n'); sys.stdout.flush()


def _input():
    raw = sys.stdin.buffer.readline(8193)
    if not raw: return None
    if len(raw) > 8192: raise DataAdapterError('unsupported')
    value = json.loads(raw)
    if not isinstance(value, dict): raise DataAdapterError('unsupported')
    return value


def main():
    """Run a single account session; no cookie export, tracing or screenshots."""
    os.umask(0o077)
    browser = None
    stage = 'launch'
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout
        config = _input()
        profile = AlphapaiProfile(config['phone'], config['password'], config['executable'], cache_ttl_seconds=0)
        timeout = min(90000, max(1, int(float(config['timeout'])*1000)))
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, executable_path=profile.browser_executable or None, timeout=timeout)
            context = browser.new_context(accept_downloads=False, service_workers='block', viewport={'width':1440, 'height':950})
            page = context.new_page(); page.set_default_timeout(min(timeout, 20000))
            authorized = False; request_count = 0; blocked_count = 0; active_path = None

            def gate(route):
                nonlocal request_count, blocked_count
                request = route.request; parsed = urlsplit(request.url); host = parsed.hostname or ''
                allowed = (parsed.scheme == 'https' and not parsed.username and not parsed.password
                    and parsed.port in (None, 443) and (host == 'rabyte.cn' or host.endswith('.rabyte.cn')))
                if not authorized:
                    request_count += 1
                    allowed = allowed and request_count <= 150 and (host == HOST or request.method == 'GET')
                    content = request.url + (request.post_data or '')
                    if host != HOST and any(s in content for s in (profile.phone, profile.password)): allowed = False
                else:
                    allowed = allowed and host == HOST and active_path and parsed.path == active_path
                if allowed: route.continue_()
                else: blocked_count += 1; route.abort()

            context.route('**/*', gate)
            # All authentication happens on the observed official HTTPS origin.
            stage = 'login'
            page.goto('https://'+HOST+'/', wait_until='domcontentloaded', timeout=timeout)
            page.get_by_text('账号密码登录', exact=True).first.click()
            if urlsplit(page.url).hostname != HOST: raise DataAdapterError('blocked_url')
            page.locator('input[placeholder*=手机号]').first.fill(profile.phone)
            page.locator('input[type=password]').first.fill(profile.password)
            agreement = page.locator('input[name=useragreement]')
            if agreement.count() and not agreement.first.is_checked(): agreement.first.check(force=True)
            page.locator('div.cp-saas-admin-button:has-text("登录")').first.click()
            try:
                page.wait_for_function("() => !!localStorage.getItem('USER_AUTH_TOKEN')", timeout=min(timeout, 30000))
            except BrowserTimeout:
                text = page.locator('body').inner_text(timeout=1000)
                challenge = any(s in text for s in ('滑块', '人机验证', '验证失败', '安全验证'))
                raise DataAdapterError('alphapai_login_challenge' if challenge else 'authentication_failed') from None
            token = page.evaluate("() => localStorage.getItem('USER_AUTH_TOKEN')")
            device = page.evaluate("() => localStorage.getItem('vt_token') || ''")
            if not isinstance(token, str) or not token: raise DataAdapterError('authentication_failed')
            page.wait_for_timeout(500)
            authorized = True
            stage = 'read'
            _emit({'ready': True, 'login_asset_requests': request_count, 'blocked_asset_requests': blocked_count})
            last = 0.0
            for _ in range(20):
                command = _input()
                if command is None: break
                operation, arguments = command['operation'], command['arguments']
                _validate(operation, arguments)
                delay = max(0, 1.3-(time.monotonic()-last))
                if delay: time.sleep(delay)
                active_path = LIST_PATH if operation == 'list' else DETAIL_PATH
                path = active_path if operation == 'list' else active_path+'?id='+arguments['id']
                reply = page.evaluate(_FETCH, {'path':path, 'payload':_payload(arguments) if operation == 'list' else None,
                    'token':token, 'device':device, 'timeout':min(30000, max(1,int(float(command['timeout'])*1000))), 'maxBytes':MAX_BYTES})
                last = time.monotonic(); active_path = None
                if reply.get('error'): raise DataAdapterError(reply['error'])
                # Strip raw errors, account fields, media URLs and secrets before stdout.
                data = _checked_reply(reply, operation, arguments, (token, device, profile.phone, profile.password))
                _emit({'http':200, 'body':{'code':200000, 'data':data}})
            context.close(); browser.close(); browser = None
    except DataAdapterError as exc: _emit({'error':exc.code})
    except ImportError: _emit({'error':'browser_dependency_missing'})
    except Exception:
        _emit({'error':'browser_unavailable' if stage == 'launch' else 'authentication_failed' if stage == 'login' else 'browser_failed'})
    finally:
        if browser is not None:
            try: browser.close()
            except Exception: pass


if __name__ == '__main__': main()
