"""Bounded Gangtise account material client; official web origin only."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import http.client
import json
from pathlib import Path
import re
import socket
import ssl
from threading import Event, Thread
from urllib.parse import unquote, urlencode

from ._interrupt import wake_blocked_socket
from .credentials import SourceConfigError, read_credentials, require_local_path
from ir_search.context import RequestStopped
from ir_search.registry import DataAdapterError

HOST = 'open.gangtise.com'
MAX_BYTES = 4 * 1024 * 1024
SEARCH_PATH = '/application/keysearch/global/search'
CATEGORIES = {'summary': 'SUMMARY', 'report': 'RSRCH_REPORT', 'opinion': 'CHIEF'}


@dataclass(frozen=True)
class GangtiseProfile:
    phone: str = field(repr=False)
    password: str = field(repr=False)
    browser_executable: str = field(default='', repr=False)
    state_dir: str = field(default='', repr=False)
    max_pages: int = 2
    max_calls: int = 12

    def __post_init__(self):
        if (not isinstance(self.phone, str) or not re.fullmatch(r'\+?[0-9]{6,20}', self.phone)
                or not isinstance(self.password, str) or not 1 <= len(self.password) <= 1024
                or any(ord(c) < 32 for c in self.password)):
            raise SourceConfigError('source_credentials_missing')
        for v in (self.browser_executable, self.state_dir):
            if not isinstance(v, str) or any(ord(c) < 32 for c in v) or (v and not Path(v).expanduser().is_absolute()):
                raise SourceConfigError()
        for v, hi in ((self.max_pages, 3), (self.max_calls, 20)):
            if type(v) is not int or not 1 <= v <= hi: raise SourceConfigError()


def gangtise_profile(*, values=None, env_file=None):
    """Opt-in credentials; accept the existing GANTISE spelling without moving secrets."""
    v = read_credentials(env_file) if values is None else values
    enabled = v.get('GANGTISE_MATERIALS_ENABLED', 'false').lower()
    if enabled not in {'true', 'false'}: raise SourceConfigError()
    if enabled == 'false': return None
    require_local_path(v, 'GANGTISE_BROWSER_EXECUTABLE', 'GANGTISE_STATE_DIR')
    try:
        return GangtiseProfile(v.get('GANGTISE_PHONE') or v.get('GANTISE_PHONE', ''),
            v.get('GANGTISE_PASSWORD') or v.get('GANTISE_PWD', ''),
            v.get('GANGTISE_BROWSER_EXECUTABLE', ''), v.get('GANGTISE_STATE_DIR', ''),
            int(v.get('GANGTISE_MAX_PAGES', '2')), int(v.get('GANGTISE_MAX_CALLS', '12')))
    except (ValueError, TypeError) as exc:
        if isinstance(exc, SourceConfigError): raise
        raise SourceConfigError() from None


def _identifier(value):
    if type(value) is int: value = str(value)
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', value):
        raise DataAdapterError('unsupported')
    return value


def _reference(category, identifier):
    if category not in CATEGORIES: raise DataAdapterError('unsupported')
    return 'gangtise://' + category + '/' + _identifier(identifier)


def _parse_reference(value):
    if not isinstance(value, str): raise DataAdapterError('unsupported')
    m = re.fullmatch(r'gangtise://(summary|report|opinion)/([A-Za-z0-9][A-Za-z0-9_-]{0,127})', value)
    if not m: raise DataAdapterError('unsupported')
    return m[1], m[2]


def _request(operation, args):
    """Allow only observed search/detail reads, never arbitrary API paths or generation."""
    if not isinstance(args, dict) or args.get('category') not in CATEGORIES: raise DataAdapterError('unsupported')
    category = args['category']
    if operation == 'search':
        if set(args) != {'category', 'query', 'page', 'size'}: raise DataAdapterError('unsupported')
        if (not isinstance(args['query'], str) or not 1 <= len(args['query']) <= 200
                or any(ord(c) < 32 for c in args['query'])
                or type(args['page']) is not int or not 1 <= args['page'] <= 3
                or type(args['size']) is not int or not 1 <= args['size'] <= 20):
            raise DataAdapterError('unsupported')
        filters = {'sort': 2}
        if category == 'summary': filters['sourceList'] = [100100178, 100100262]
        return SEARCH_PATH, {'keyword': args['query'], 'srcCodes': [CATEGORIES[category]],
            'pageNum': args['page']-1, 'pageSize': args['size'], 'isPartial': False,
            'maxPreSize': args['size'], 'filters': filters}
    if operation != 'detail' or set(args) != {'category', 'id'}: raise DataAdapterError('unsupported')
    identifier = _identifier(args['id'])
    if category == 'summary': return '/application/summary/queryById?id='+identifier, None
    if category == 'report': return '/application/glory/research/'+identifier, None
    return '/application/glory/chief/v3/queryOpinionList', {'condition': {
        'must': {'id': identifier}, 'bizParams': {'complete': True}}}


def _error_code(status, body=None):
    body = body if isinstance(body, dict) else {}
    code = str(body.get('code', ''))
    msg = str(body.get('msg', ''))[:1000].lower()
    if code in {'899999', '800009'} or any(s in msg for s in ('新设备', '验证码', '人机', 'captcha')):
        return 'gangtise_login_challenge'
    if status == 401 or code in {'910001', '910000', '800012'} or any(s in msg for s in ('bad credentials', 'token', '未登录', '登录过期', '密码错误')):
        return 'authentication_failed'
    if code == '903301': return 'quota'
    if code in {'10011401', '10011402'}: return 'entitlement_denied'
    if status == 429 or any(s in msg for s in ('频繁', '限流')): return 'rate_limit'
    if any(s in msg for s in ('额度', '余额不足', 'quota')): return 'quota'
    if status in {402, 403} or any(s in msg for s in ('权限', '订阅', '购买', '会员', 'locked')): return 'entitlement_denied'
    if status == 404: return 'not_found'
    if 300 <= status < 400: return 'blocked_url'
    return 'network' if status >= 500 else 'upstream_schema'


def _clean_row(row):
    if not isinstance(row, dict): raise DataAdapterError('upstream_schema')
    # Source field allowlist: account data, signed links and arbitrary nested data are dropped.
    names = ('id', 'rptId', 'title', 'brief', 'details', 'content', 'msgText', 'msgTime', 'pubTime',
             'partyName', 'brokerName', 'issuerStmt', 'username', 'source', 'duration',
             'msgType', 'hasPermission', 'permission', 'isPreview', 'totalWords')
    clean = {k: row[k] for k in names if k in row and isinstance(row[k], (str, int, float, bool, type(None)))}
    if isinstance(row.get('initiator'), list):
        clean['initiator'] = [{k: r[k] for k in ('partyName', 'cnName') if isinstance(r.get(k), str)}
                              for r in row['initiator'][:20] if isinstance(r, dict)]
    if isinstance(row.get('msgText'), list):
        if len(row['msgText']) > 20: raise DataAdapterError('upstream_schema')
        clean['msgText'] = []
        for part in row['msgText']:
            if not isinstance(part, dict): raise DataAdapterError('upstream_schema')
            clean['msgText'].append({k: part[k] for k in ('usage', 'url', 'content')
                                    if k in part and isinstance(part[k], (str, int))})
    elif isinstance(row.get('msgText'), dict):
        clean['msgText'] = {k: row['msgText'][k] for k in ('title', 'content', 'description')
                            if isinstance(row['msgText'].get(k), str)}
    elif isinstance(clean.get('msgText'), str):
        try: nested = json.loads(clean['msgText'])
        except (ValueError, TypeError): raise DataAdapterError('upstream_schema') from None
        if not isinstance(nested, dict): raise DataAdapterError('upstream_schema')
        clean['msgText'] = {k: nested[k] for k in ('title', 'content', 'description') if isinstance(nested.get(k), str)}
    return clean


def _checked_reply(reply, operation, args, secrets=()):
    if not isinstance(reply, dict) or type(reply.get('http')) is not int: raise DataAdapterError('upstream_schema')
    body = reply.get('body')
    if (reply['http'] != 200 or not isinstance(body, dict) or body.get('status') is not True
            or str(body.get('code')) not in {'000000', '10010000'}):
        raise DataAdapterError(_error_code(reply['http'], body))
    try: raw = json.dumps(body, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, RecursionError): raise DataAdapterError('upstream_schema') from None
    if len(raw.encode()) > MAX_BYTES: raise DataAdapterError('response_too_large')
    if any(v and v in raw for v in secrets): raise DataAdapterError('upstream_schema')
    data = body.get('data')
    if operation == 'search':
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict): raise DataAdapterError('upstream_schema')
        group = data[0]
        if group.get('srcCode') != CATEGORIES[args['category']]: raise DataAdapterError('upstream_schema')
        rows, total = group.get('list'), group.get('total')
        if not isinstance(rows, list) or len(rows) > args['size'] or type(total) is not int or total < len(rows):
            raise DataAdapterError('upstream_schema')
        return {'list': [_clean_row(r) for r in rows], 'total': total}
    if args['category'] in {'opinion', 'report'}:
        if not isinstance(data, list) or len(data) != 1: raise DataAdapterError('not_found' if data == [] else 'upstream_schema')
        data = data[0]
    if data is None: raise DataAdapterError('not_found')
    clean = _clean_row(data)
    try: identifier = _identifier(clean.get('rptId') if args['category'] == 'report' and clean.get('rptId') is not None else clean.get('id'))
    except DataAdapterError: raise DataAdapterError('upstream_schema') from None
    if identifier != args['id']: raise DataAdapterError('upstream_schema')
    if clean.get('hasPermission') is False: raise DataAdapterError('entitlement_denied')
    return clean


def _file_path(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 1500: raise DataAdapterError('upstream_schema')
    decoded = unquote(value)
    if (any(ord(c) < 32 for c in decoded) or any(c in decoded for c in '?&#\\:%')
            or '..' in decoded.split('/') or decoded.startswith('//')
            or not decoded.lower().endswith(('.txt', '.html', '.htm'))):
        raise DataAdapterError('unsupported')
    return value


def _http(path, payload, token, *, context, text=False):
    """Verified TLS, fixed host, no redirects, bounded body and cooperative cancellation."""
    connection = active_socket = watcher = None
    finished = Event()
    try:
        context.check_active()
        tls = ssl.create_default_context(); tls.minimum_version = ssl.TLSVersion.TLSv1_2
        connection = http.client.HTTPSConnection(HOST, timeout=context.remaining_seconds(), context=tls)
        def cancel():
            while not finished.wait(.05):
                try: context.check_active()
                except RequestStopped:
                    sock = active_socket or connection.sock
                    wake_blocked_socket(sock)
                    return
        watcher = Thread(target=cancel, daemon=True); watcher.start()
        connection.connect(); active_socket = connection.sock
        context.check_active(); active_socket.settimeout(context.remaining_seconds())
        connection.request('GET' if payload is None else 'POST', path,
            body=None if payload is None else json.dumps(payload).encode(),
            headers={'Authorization': 'Bearer '+token, 'Content-Type': 'application/json',
                     'Accept': 'application/json', 'Accept-Encoding': 'identity', 'User-Agent': 'ir-search/0.1'})
        response = connection.getresponse()
        if response.status != 200: raise DataAdapterError(_error_code(response.status))
        content_type = response.getheader('Content-Type', '').split(';')[0].strip().lower()
        accepted = {'application/json', 'text/plain', 'text/html', 'application/octet-stream'} if text else {'application/json'}
        if response.getheader('Content-Encoding', 'identity') != 'identity' or content_type not in accepted:
            raise DataAdapterError('upstream_schema')
        length = response.getheader('Content-Length')
        if length is not None and (not length.isdigit() or int(length) > MAX_BYTES): raise DataAdapterError('response_too_large')
        raw = bytearray()
        while True:
            context.check_active(); active_socket.settimeout(context.remaining_seconds())
            block = response.read1(min(65536, MAX_BYTES+1-len(raw)))
            raw.extend(block)
            if len(raw) > MAX_BYTES: raise DataAdapterError('response_too_large')
            if not block: break
        context.check_active()
        if text:
            decoded = raw.decode('utf-8-sig')
            if decoded.lstrip().startswith(('{', '[')) or content_type == 'application/json':
                try: error = json.loads(decoded)
                except ValueError: raise DataAdapterError('upstream_schema') from None
                raise DataAdapterError(_error_code(200, error))
            if (re.search(r'<input[^>]+type\s*=\s*["\']?password', decoded, re.I)
                    or ('Gangtise投研' in decoded and '欢迎使用' in decoded)
                    or '检测到新设备登录' in decoded):
                raise DataAdapterError('authentication_failed')
            return decoded
        return {'http': 200, 'body': json.loads(raw)}
    except (DataAdapterError, RequestStopped): raise
    except ssl.SSLError: raise DataAdapterError('tls_error') from None
    except (socket.timeout, TimeoutError):
        context.check_active(); raise DataAdapterError('timeout') from None
    except (ValueError, UnicodeError, RecursionError): raise DataAdapterError('upstream_schema') from None
    except Exception:
        context.check_active(); raise DataAdapterError('network') from None
    finally:
        finished.set()
        if connection is not None: connection.close()
        if watcher is not None: watcher.join(timeout=.2)


@dataclass(frozen=True)
class GangtiseResponse:
    data: dict = field(repr=False)
    fetched_at: datetime


class GangtiseClient:
    """Request-local account token, strict read allowlist, no automatic retry loop."""
    def __init__(self, profile, *, transport=None, authenticator=None):
        if not isinstance(profile, GangtiseProfile): raise ValueError('GangtiseProfile required')
        from .gangtise_auth import _get_token
        self.profile = profile
        self._transport, self._auth = transport or _http, authenticator or _get_token
        self._token, self.calls, self._terminal = '', 0, None
        self._files = set()

    def read(self, operation, arguments, *, context):
        """Read one bounded search page or one identified stored material."""
        path, payload = _request(operation, arguments)
        context.check_active()
        if context.account_scope != 'default': raise DataAdapterError('entitlement_denied')
        if self._terminal: raise DataAdapterError(self._terminal)
        if self.calls >= self.profile.max_calls: raise DataAdapterError('gangtise_call_budget_exhausted')
        self.calls += 1
        try:
            if not self._token:
                if context.max_operations-context.operations < 2: raise RequestStopped('operation_budget_exhausted')
                from .gangtise_auth import _token
                self._token = _token(self._auth(self.profile, context=context))
            if not context._wait_for_public_host(HOST): raise DataAdapterError('rate_limit')
            context.begin_operation()
            reply = self._transport(path, payload, self._token, context=context)
            context.check_active()
            data = _checked_reply(reply, operation, arguments, (self._token, self.profile.phone, self.profile.password))
            if operation == 'detail' and arguments['category'] == 'summary':
                for item in data.get('msgText', []) if isinstance(data.get('msgText'), list) else []:
                    try: path = _file_path(item.get('url'))
                    except DataAdapterError: continue
                    self._files.add((arguments['id'], path))
            return GangtiseResponse(data, datetime.now(timezone.utc))
        except DataAdapterError as exc:
            if exc.code not in {'not_found', 'entitlement_denied', 'unsupported', 'upstream_schema'}:
                self._terminal = exc.code
            if exc.code == 'authentication_failed' and self._token:
                from .gangtise_auth import _invalidate_token
                _invalidate_token(self.profile, self._token)
            self.close(); raise

    def _summary_text(self, identifier, path, *, context):
        if (_identifier(identifier), _file_path(path)) not in self._files: raise DataAdapterError('blocked_url')
        context.check_active()
        if context.account_scope != 'default': raise DataAdapterError('entitlement_denied')
        if self._terminal: raise DataAdapterError(self._terminal)
        if self.calls >= self.profile.max_calls: raise DataAdapterError('gangtise_call_budget_exhausted')
        self.calls += 1
        if not context._wait_for_public_host(HOST): raise DataAdapterError('rate_limit')
        context.begin_operation()
        endpoint = '/application/summary/download?'+urlencode({'id':identifier, 'path':path})
        try: value = self._transport(endpoint, None, self._token, context=context, text=True)
        except DataAdapterError as exc:
            if exc.code in {'authentication_failed', 'quota', 'rate_limit'}: self._terminal = exc.code
            if exc.code == 'authentication_failed':
                from .gangtise_auth import _invalidate_token
                _invalidate_token(self.profile, self._token)
            raise
        context.check_active()
        if not isinstance(value, str) or any(s and s in value for s in (self._token, self.profile.phone, self.profile.password)):
            raise DataAdapterError('upstream_schema')
        if len(value.encode()) > MAX_BYTES: raise DataAdapterError('response_too_large')
        return value

    def close(self):
        """Drop the request-local token; only explicit private auth state persists."""
        self._token = ''
        self._files.clear()
