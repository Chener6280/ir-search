"""Optional, isolated Xueqiu article browser; no Desktop skill dependency."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from zoneinfo import ZoneInfo

from ir_search.context import RequestStopped
from ir_search.registry import DataAdapterError
from .credentials import read_credentials, SourceConfigError
from .platform_documents import _platform_url, _document, _cookie
from .web_browser import _environment, _stop


@dataclass(frozen=True)
class XueqiuReadProfile:
    mode: str = 'http'
    browser_use_cookie: bool = False
    browser_headless: bool = True
    browser_channel: str = 'chromium'


def xueqiu_read_profile(*, values=None, env_file=None):
    """Explicit backend selection; enabling a browser never grants login actions."""
    values = read_credentials(env_file) if values is None else values
    mode = values.get('XUEQIU_READ_MODE', 'http')
    cookie = values.get('XUEQIU_BROWSER_USE_COOKIE', 'false').lower()
    headless = values.get('XUEQIU_BROWSER_HEADLESS', 'true').lower()
    channel = values.get('XUEQIU_BROWSER_CHANNEL', 'chromium')
    if mode not in {'http', 'browser'} or cookie not in {'true', 'false'} or headless not in {'true', 'false'}:
        raise SourceConfigError()
    if channel not in {'chromium', 'chrome'}: raise SourceConfigError()
    return XueqiuReadProfile(mode, cookie == 'true', headless == 'true', channel)


def _dependency():
    try:
        parts = tuple(int(p) for p in version('playwright').split('.')[:2])
    except PackageNotFoundError: raise DataAdapterError('browser_dependency_missing') from None
    except ValueError: raise DataAdapterError('browser_version_unsupported') from None
    if parts < (1, 49): raise DataAdapterError('browser_version_unsupported')


def _run(url, *, context, max_chars, cookie, headless=True, channel='chromium'):
    context.begin_operation()
    remaining = context.max_operations - context.operations
    if remaining < 1: raise RequestStopped('operation_budget_exhausted')
    package = str(Path(__file__).resolve().parents[2])
    bootstrap = 'import sys; sys.path.insert(0,sys.argv.pop(1)); from ir_search.infrastructure._xueqiu_browser_worker import main; main()'
    with tempfile.TemporaryDirectory(prefix='ir-search-xueqiu-') as directory:
        os.chmod(directory, 0o700)
        output = Path(directory)/'result.json'
        payload = {'url': url, 'timeout': context.remaining_seconds(), 'operations': remaining,
                   'output': str(output), 'cookie': cookie, 'max_chars': max_chars, 'headless': headless, 'channel': channel}
        process = subprocess.Popen([sys.executable, '-I', '-c', bootstrap, package], cwd=directory,
            env=_environment(directory), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=os.name == 'posix', creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
        accounted = False
        try:
            process.stdin.write(json.dumps(payload).encode()); process.stdin.close()
            while process.poll() is None:
                context.check_active()
                if output.exists() and output.stat().st_size > 3*1024*1024: raise DataAdapterError('response_too_large')
                time.sleep(.05)
            context.check_active()
            if not output.exists() or output.stat().st_size > 3*1024*1024: raise DataAdapterError('browser_failed')
            data = json.loads(output.read_text())
            operations = data.get('operations')
            if type(operations) is not int or not 0 <= operations <= remaining: raise DataAdapterError('browser_failed')
            context.operations += operations; accounted = True
            code = data.get('error')
            if code in {'cancelled', 'deadline_exceeded', 'operation_budget_exhausted'}: raise RequestStopped(code)
            if code: raise DataAdapterError(code if code in DataAdapterError.KINDS else 'browser_failed')
            return data
        finally:
            _stop(process)
            if not accounted: context.operations += remaining


def read_xueqiu_browser_document(url, *, context, max_chars=20000):
    """Read only a main article's visible DOM using a disposable browser session."""
    if type(max_chars) is not int or not 1 <= max_chars <= 100000: raise ValueError('Invalid text limit')
    platform, canonical, _ = _platform_url(url)
    if platform != 'xueqiu': raise DataAdapterError('unsupported')
    context.check_active()
    try: profile = xueqiu_read_profile()
    except SourceConfigError: raise DataAdapterError('source_config_error') from None
    _dependency()
    cookie = _cookie('XUEQIU_COOKIE') if profile.browser_use_cookie else ''
    if profile.browser_use_cookie and not cookie: raise DataAdapterError('no_credential')
    try:
        data = _run(canonical, context=context, max_chars=max_chars, cookie=cookie,
                    headless=profile.browser_headless, channel=profile.browser_channel)
        if data.get('url') != canonical: raise DataAdapterError('blocked_url')
        for name, limit in (('title', 4000), ('text', max_chars), ('publisher', 500), ('publication', 200), ('publication_url', 8192)):
            if not isinstance(data.get(name), str) or len(data[name]) > limit: raise DataAdapterError('upstream_schema')
        if not data['text'].strip(): raise DataAdapterError('no_extracted_text')
        if type(data.get('truncated')) is not bool: raise DataAdapterError('upstream_schema')
        browser = data.get('browser')
        if (not isinstance(browser, dict) or browser.get('network') != 'public_dns_pinned_connect'
                or browser.get('tls_verified') is not True or browser.get('login_performed') is not False
                or any(type(browser.get(k)) is not int or browser[k] < 0 for k in ('blocked_requests', 'wire_bytes'))):
            raise DataAdapterError('upstream_schema')
        # Rebuild diagnostics from known fields; never forward arbitrary child state.
        details = {'content_origin': 'community_post', 'source_attribution_preserved': True,
            'comments_included': False, 'read_mode': 'browser', 'browser': {
                'backend': 'playwright_chromium', 'network': 'public_dns_pinned_connect', 'tls_verified': True,
                'blocked_requests': browser['blocked_requests'], 'wire_bytes': browser['wire_bytes'],
                'cookie_mode': 'explicit_env' if cookie else 'anonymous', 'login_performed': False,
                'channel': profile.browser_channel, 'headless': profile.browser_headless, 'experimental': True}}
        if cookie:
            secrets = [p.partition('=')[2] for p in cookie.split(';') if len(p.partition('=')[2]) > 12]
            if any(secret in data[field] for secret in secrets
                   for field in ('text', 'title', 'publisher', 'publication', 'publication_url')):
                raise DataAdapterError('upstream_schema')
        published = None
        warnings = ['community_content_not_verified', 'reposts_not_independent_corroboration']
        match = re.fullmatch(r'发布于\s*(\d{4}-\d\d-\d\d \d\d:\d\d)(?::\d\d)?', data['publication'].strip())
        publication_bound = False
        try: publication_bound = _platform_url(data['publication_url'])[:2] == ('xueqiu', canonical)
        except DataAdapterError: pass
        if match and publication_bound:
            try: published = datetime.fromisoformat(match[1]).replace(tzinfo=ZoneInfo('Asia/Shanghai'))
            except ValueError: warnings.append('publication_date_invalid')
        if not published: warnings.append('published_date_unknown')
        if data['truncated']: warnings.append('text_truncated')
        context.check_active()
        doc = _document(canonical, platform, ''.join(data['title'].splitlines()), data['text'], datetime.now(timezone.utc),
                        published=published, publisher=data['publisher'], details=details, warnings=warnings)
        doc.extraction_method = 'xueqiu_browser_visible_article'
        return doc
    except (DataAdapterError, RequestStopped): raise
    except Exception: raise DataAdapterError('browser_failed') from None
