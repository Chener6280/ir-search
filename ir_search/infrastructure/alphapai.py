"""Account-only AlphaPai stored-material transport; no Open API or generated requests."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import tempfile
from threading import Thread

from ir_search.context import RequestStopped
from ir_search.registry import DataAdapterError
from .credentials import SourceConfigError, read_credentials

MAX_BYTES = 4 * 1024 * 1024
LIST_PATH = '/external/alpha/api/reading/roadshow/summary/list'
DETAIL_PATH = '/external/alpha/api/reading/roadshow/summary/detail'
HOST = 'alphapai-web.rabyte.cn'


@dataclass(frozen=True)
class AlphapaiProfile:
    phone: str = field(repr=False)
    password: str = field(repr=False)
    browser_executable: str = field(default='', repr=False)
    cache_dir: str = field(default='', repr=False)
    cache_ttl_seconds: int = 3600
    max_pages: int = 2
    max_calls: int = 12

    def __post_init__(self):
        if (not isinstance(self.phone, str) or not re.fullmatch(r'\+?[0-9]{6,20}', self.phone)
                or not isinstance(self.password, str) or not 1 <= len(self.password) <= 1024
                or any(ord(c) < 32 for c in self.password)):
            raise SourceConfigError('source_credentials_missing')
        for value in (self.browser_executable, self.cache_dir):
            if not isinstance(value, str) or any(ord(c) < 32 for c in value): raise SourceConfigError()
            if value and not Path(value).expanduser().is_absolute(): raise SourceConfigError()
        for value, lo, hi in ((self.cache_ttl_seconds, 0, 86400), (self.max_pages, 1, 3), (self.max_calls, 1, 20)):
            if type(value) is not int or not lo <= value <= hi: raise SourceConfigError()


def alphapai_profile(*, values=None, env_file=None):
    """Read opt-in account credentials only; no API key is required or repurposed."""
    values = read_credentials(env_file) if values is None else values
    enabled = values.get('ALPHAPAI_MATERIALS_ENABLED', 'false').lower()
    if enabled not in {'true', 'false'}: raise SourceConfigError()
    if enabled == 'false': return None
    try:
        return AlphapaiProfile(values.get('ALPHA_PIE_PHONE', ''), values.get('ALPHA_PIE_PWD', ''),
            values.get('ALPHAPAI_BROWSER_EXECUTABLE', ''), values.get('ALPHAPAI_CACHE_DIR', ''),
            int(values.get('ALPHAPAI_CACHE_TTL_SECONDS', '3600')), int(values.get('ALPHAPAI_MAX_PAGES', '2')),
            int(values.get('ALPHAPAI_MAX_CALLS', '12')))
    except (ValueError, TypeError) as exc:
        if isinstance(exc, SourceConfigError): raise
        raise SourceConfigError() from None


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,256}', value):
        raise DataAdapterError('unsupported')
    return value


def _reference(identifier, kind='summary'):
    if kind not in {'summary', 'transcript'}: raise DataAdapterError('unsupported')
    return 'alphapai://meeting/' + _identifier(identifier) + '/' + kind


def _parse_reference(value):
    match = re.fullmatch(r'alphapai://meeting/([A-Za-z0-9_-]{16,256})/(summary|transcript)', value or '')
    if not match: raise DataAdapterError('unsupported')
    return match[1], match[2]


def _validate(operation, arguments):
    if not isinstance(arguments, dict): raise DataAdapterError('unsupported')
    if operation == 'detail':
        if set(arguments) != {'id'}: raise DataAdapterError('unsupported')
        _identifier(arguments['id']); return
    if operation != 'list' or set(arguments) != {'query', 'start', 'end', 'page', 'size', 'symbols'}:
        raise DataAdapterError('unsupported')
    if (not isinstance(arguments['query'], str) or not 1 <= len(arguments['query']) <= 200
            or any(ord(c) < 32 for c in arguments['query'])): raise DataAdapterError('unsupported')
    try:
        for name in ('start', 'end'):
            if not re.fullmatch(r'\d{4}-\d\d-\d\d', arguments[name]): raise ValueError()
        start, end = (date.fromisoformat(arguments[k]) for k in ('start', 'end'))
        if not 0 <= (end-start).days <= 366: raise ValueError()
    except (ValueError, TypeError): raise DataAdapterError('unsupported') from None
    if (type(arguments['page']) is not int or not 1 <= arguments['page'] <= 3
            or type(arguments['size']) is not int or not 1 <= arguments['size'] <= 20
            or not isinstance(arguments['symbols'], (tuple, list)) or len(arguments['symbols']) > 5
            or any(not isinstance(s, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.\-]{0,29}', s) for s in arguments['symbols'])):
        raise DataAdapterError('unsupported')


def _payload(arguments):
    return {'pageNum': arguments['page'], 'pageSize': arguments['size'],
        'beginTime': arguments['start']+' 00:00:00', 'endTime': arguments['end']+' 23:59:59',
        'marketType': [], 'marketTypeV2': '', 'featureV2': [], 'industry': [], 'stock': list(arguments['symbols']),
        'hasRadio': False, 'priceMovementSort': '', 'institution': [], 'durationCategory': '',
        'word': arguments['query'], 'isPrivate': False, 'filterNoPermission': False}


def _error_code(status, body):
    if status == 429: return 'rate_limit'
    if status == 401: return 'authentication_failed'
    if status in {402, 403}: return 'entitlement_denied'
    if status == 404: return 'not_found'
    if 300 <= status < 400: return 'blocked_url'
    message = str(body.get('message', ''))[:1000].lower() if isinstance(body, dict) else ''
    if any(s in message for s in ('查看上限', '额度', 'quota', '余额不足')): return 'quota'
    if any(s in message for s in ('验证码', '滑块', '人机验证')): return 'alphapai_login_challenge'
    if any(s in message for s in ('未登录', '登录失效', '登录过期', '密码错误', 'token', '请登录')): return 'authentication_failed'
    if any(s in message for s in ('权限', '付费', '订阅', '会员')): return 'entitlement_denied'
    if any(s in message for s in ('频繁', '稍后重试', '限流')): return 'rate_limit'
    if any(s in message for s in ('不存在', '已删除')): return 'not_found'
    if status >= 500 or (isinstance(body, dict) and body.get('code') in {500000, 500020}): return 'network'
    return 'upstream_schema'


def _clean_row(row, *, detail=False):
    """Only source fields can cross the browser boundary; no account/media URLs."""
    if not isinstance(row, dict): raise DataAdapterError('upstream_schema')
    names = ('id', 'title', 'publishInstitution', 'date', 'roadshowDate', 'content', 'aiContent',
             'stock', 'meetingLabel', 'hasPermission', 'freeAccess', 'isFree', 'recLength')
    result = {k: row[k] for k in names if k in row}
    if detail:
        for k in ('aiSummary', 'mtSummary', 'mtSummarySwitchOpen'):
            v = row.get(k)
            if isinstance(v, dict):
                result[k] = {name: v[name] for name in ('content', 'wordCount', 'duration') if name in v}
        permission = row.get('sharePermission')
        if isinstance(permission, dict): result['sharePermission'] = {'hasPermission': permission.get('hasPermission')}
    return result


def _checked_reply(reply, operation, arguments, secrets=()):
    if not isinstance(reply, dict) or type(reply.get('http')) is not int: raise DataAdapterError('upstream_schema')
    body = reply.get('body')
    if reply['http'] != 200 or not isinstance(body, dict) or type(body.get('code')) is not int or body['code'] != 200000:
        raise DataAdapterError(_error_code(reply['http'], body))
    raw = json.dumps(body, ensure_ascii=False, allow_nan=False)
    if any(s and s in raw for s in secrets): raise DataAdapterError('upstream_schema')
    data = body.get('data')
    if not isinstance(data, dict): raise DataAdapterError('upstream_schema')
    if operation == 'list':
        rows = data.get('list'); total = data.get('total')
        if (not isinstance(rows, list) or len(rows) > arguments['size']
                or type(total) is not int or total < 0): raise DataAdapterError('upstream_schema')
        return {'list': [_clean_row(r) for r in rows], 'total': total}
    # The web API re-encrypts IDs even between a list and its detail response.
    # Bind this reply to the actual HTTPS request, not equality of rotating IDs.
    try: _identifier(data.get('id'))
    except DataAdapterError: raise DataAdapterError('upstream_schema') from None
    result = _clean_row(data, detail=True)
    result['_requested_id'] = arguments['id']
    return result


@dataclass(frozen=True)
class AlphapaiResponse:
    data: dict = field(repr=False)
    fetched_at: datetime
    cache_state: str = 'fresh'


class _BrowserSession:
    def __init__(self, profile, context):
        from importlib.util import find_spec
        from .web_browser import _environment
        if find_spec('playwright') is None: raise DataAdapterError('browser_dependency_missing')
        self._directory = tempfile.TemporaryDirectory(prefix='ir-search-alphapai-')
        self._process = None
        self._events = queue.Queue(maxsize=2)
        package = str(Path(__file__).resolve().parents[2])
        boot = 'import sys; sys.path.insert(0,sys.argv.pop(1)); from ir_search.infrastructure._alphapai_worker import main; main()'
        try:
            env = _environment(self._directory.name)
            env['TMPDIR'] = self._directory.name
            self._process = subprocess.Popen([sys.executable, '-I', '-c', boot, package],
                cwd=self._directory.name, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, start_new_session=os.name == 'posix',
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
            self._thread = Thread(target=self._read, daemon=True); self._thread.start()
            ready = self._exchange({'phone': profile.phone, 'password': profile.password,
                            'executable': profile.browser_executable, 'timeout': context.remaining_seconds()}, context)
            if ready.get('ready') is not True: raise DataAdapterError('browser_failed')
        except Exception:
            self.close(); raise

    def _read(self):
        process = self._process
        try:
            while True:
                raw = process.stdout.readline(MAX_BYTES+1)
                if not raw or len(raw) > MAX_BYTES:
                    self._events.put_nowait({'error': 'response_too_large' if raw else 'browser_failed'}); return
                self._events.put_nowait(json.loads(raw))
        except Exception:
            try: self._events.put_nowait({'error': 'browser_failed'})
            except queue.Full: pass

    def _exchange(self, payload, context):
        context.check_active()
        try:
            self._process.stdin.write(json.dumps(payload).encode()+b'\n'); self._process.stdin.flush()
            while True:
                context.check_active()
                try: reply = self._events.get(timeout=min(.05, context.remaining_seconds()))
                except queue.Empty: continue
                if not isinstance(reply, dict): raise DataAdapterError('browser_failed')
                code = reply.get('error')
                if code: raise DataAdapterError(code if code in DataAdapterError.KINDS else 'browser_failed')
                return reply
        except (DataAdapterError, RequestStopped): raise
        except Exception: raise DataAdapterError('browser_failed') from None

    def read(self, operation, arguments, context):
        return self._exchange({'operation': operation, 'arguments': arguments,
                               'timeout': context.remaining_seconds()}, context)

    def close(self):
        from .web_browser import _stop
        if self._process is not None:
            try: _stop(self._process)
            except Exception: pass
            for pipe in (self._process.stdin, self._process.stdout):
                try: pipe.close()
                except Exception: pass
            self._process = None
        self._directory.cleanup()


class AlphapaiClient:
    """One disposable login per operation batch, with private bounded detail cache."""
    def __init__(self, profile, *, session_factory=None, cache=None):
        if not isinstance(profile, AlphapaiProfile): raise ValueError('AlphapaiProfile required')
        from .alphapai_cache import _DetailCache
        self.profile, self._factory = profile, session_factory or _BrowserSession
        self._cache = cache if cache is not None else _DetailCache(profile)
        self._session, self.calls, self._terminal_error = None, 0, None

    def read(self, operation, arguments, *, context):
        """Read lists or stored details only; errors never expose vendor response text."""
        _validate(operation, arguments); context.check_active()
        if context.account_scope != 'default': raise DataAdapterError('entitlement_denied')
        if self._terminal_error: raise DataAdapterError(self._terminal_error)
        if self.calls >= self.profile.max_calls: raise DataAdapterError('alphapai_call_budget_exhausted')
        self.calls += 1
        if operation == 'detail':
            cached = self._cache.get(arguments['id'])
            if cached is not None:
                context.check_active(); return cached
        if self._session is None:
            if context.max_operations-context.operations < 2: raise RequestStopped('operation_budget_exhausted')
            context.begin_operation()
            self._session = self._factory(self.profile, context)
        context.begin_operation()
        try:
            reply = self._session.read(operation, arguments, context)
            context.check_active()
            data = _checked_reply(reply, operation, arguments, (self.profile.phone, self.profile.password))
        except DataAdapterError as exc:
            self._terminal_error = exc.code; self.close(); raise
        except RequestStopped:
            self.close(); raise
        except (ValueError, TypeError, RecursionError): raise DataAdapterError('upstream_schema') from None
        result = AlphapaiResponse(data, datetime.now(timezone.utc))
        if operation == 'detail':
            try: self._cache.put(arguments['id'], result)
            except DataAdapterError:
                result = AlphapaiResponse(data, result.fetched_at, 'write_failed')
        return result

    def close(self):
        """Destroy the ephemeral browser and account session; no storage state saved."""
        if self._session is not None:
            self._session.close(); self._session = None
