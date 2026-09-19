"""Read-only access to an explicitly configured local xiaohongshu-mcp service.

The optional service owns its browser/session. The installable SDK neither installs
software nor discovers browser cookies. Per-note access tokens remain private.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import hmac
import http.client
import json
from pathlib import Path
import re
import socket
from urllib.parse import urlsplit

from .credentials import SourceConfigError, credentials_path, read_credentials
from .private_files import _private_dir, _private_read, _private_write, _directory_lock
from ir_search.registry import DataAdapterError

MAX_BYTES = 4 * 1024 * 1024
SORTS = {'latest': '最新', 'relevance': '综合', 'likes': '最多点赞'}


@dataclass(frozen=True)
class XhsProfile:
    base_url: str = 'http://127.0.0.1:18060'
    token: str = field(default='', repr=False)
    cache_dir: str = ''

    def __post_init__(self):
        try:
            u = urlsplit(self.base_url)
            if (u.scheme != 'http' or u.hostname != '127.0.0.1' or u.username or u.password
                    or u.path or u.query or u.fragment or not u.port or not 1024 <= u.port <= 65535
                    or self.base_url != 'http://127.0.0.1:'+str(u.port)):
                raise ValueError()
            if not isinstance(self.token, str) or not re.fullmatch(r'[A-Za-z0-9_\-.~]{16,512}', self.token):
                raise ValueError()
            if not isinstance(self.cache_dir, str) or any(ord(c)<32 for c in self.cache_dir): raise ValueError()
        except (ValueError, TypeError, AttributeError): raise SourceConfigError('invalid_source_config') from None


def xhs_profile(*, values=None, env_file=None):
    """Read opt-in settings without contacting the service or exposing tokens."""
    values = read_credentials(env_file) if values is None else values
    enabled = values.get('XHS_MATERIALS_ENABLED', 'false').lower()
    if enabled not in {'true', 'false'}: raise SourceConfigError()
    if enabled == 'false': return None
    root = Path(values.get('XHS_CACHE_DIR') or '.local/xhs-cache').expanduser()
    if not root.is_absolute(): root = credentials_path(env_file).parent / root
    return XhsProfile(values.get('XHS_BACKEND_URL', 'http://127.0.0.1:18060'),
                      values.get('XHS_BACKEND_TOKEN', ''), str(root.absolute()))


def _id(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{24}', value):
        raise DataAdapterError('upstream_schema')
    return value


def _reference(identifier):
    return 'xhs://note/' + _id(identifier)


def _parse_reference(reference):
    try:
        if not isinstance(reference, str): raise ValueError()
        u = urlsplit(reference)
        if u.query or u.username or u.password or u.port: raise ValueError()
        if u.fragment and not re.fullmatch(r'comment=[A-Za-z0-9_-]{1,128}', u.fragment): raise ValueError()
        if u.scheme == 'xhs' and u.netloc == 'note': identifier = u.path[1:]
        elif u.scheme == 'https' and u.netloc == 'www.xiaohongshu.com' and u.path.startswith('/explore/'):
            identifier = u.path[len('/explore/'):]
        else: raise ValueError()
        return _id(identifier)
    except (ValueError, TypeError, DataAdapterError): raise DataAdapterError('blocked_url') from None


def _token(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_+/=.-]{1,2048}', value):
        raise DataAdapterError('upstream_schema')
    return value


@dataclass(frozen=True)
class XhsResponse:
    data: dict
    fetched_at: datetime
    cache_state: str = 'fresh'


class _Cache:
    def __init__(self, profile):
        self.key = hashlib.sha256((profile.base_url+'\0'+profile.token).encode()).digest()
        root = Path(profile.cache_dir).expanduser() if profile.cache_dir else Path.home()/'.cache'/'ir-search'/'xhs'
        self.root = root.absolute() / hashlib.sha256(self.key).hexdigest()[:24]

    def _ensure(self):
        try:
            if any(p.is_symlink() for p in (self.root, *self.root.parents)): raise OSError()
            _private_dir(self.root.parent)
            _private_dir(self.root)
        except OSError: raise DataAdapterError('xhs_cache_unavailable') from None

    def _path(self, key):
        return self.root / (hmac.new(self.key, key.encode(), hashlib.sha256).hexdigest()+'.json')

    @staticmethod
    def _encode(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()

    def get(self, key, ttl):
        self._ensure()
        try:
            envelope = json.loads(_private_read(self._path(key), MAX_BYTES))
            record = envelope['record']
            if not hmac.compare_digest(envelope['hmac'], hmac.new(self.key,self._encode(record),hashlib.sha256).hexdigest()): raise ValueError()
            fetched = datetime.fromisoformat(record['fetched_at'])
            if fetched.utcoffset() is None or record['key'] != key or not isinstance(record['data'],dict): raise ValueError()
            age = (datetime.now(timezone.utc)-fetched).total_seconds()
            if age < 0 or age > ttl: return None
            return XhsResponse(record['data'], fetched, 'hit')
        except FileNotFoundError: return None
        except (OSError, ValueError, TypeError, KeyError, RecursionError): raise DataAdapterError('xhs_cache_invalid') from None

    def put(self, key, reply):
        self._ensure()
        try:
            record = {'key':key, 'fetched_at':reply.fetched_at.isoformat(), 'data':reply.data}
            raw = self._encode({'record':record, 'hmac':hmac.new(self.key,self._encode(record),hashlib.sha256).hexdigest()})
            if len(raw)>MAX_BYTES: raise ValueError()
            entries = sorted((p for p in self.root.glob('*.json') if not p.is_symlink()), key=lambda p:p.stat().st_mtime)
            total = sum(p.stat().st_size for p in entries)
            while entries and (len(entries)>=256 or total+len(raw)>64*1024*1024):
                p=entries.pop(0); total-=p.stat().st_size; p.unlink()
            _private_write(self._path(key), raw)
        except (OSError, ValueError, TypeError): raise DataAdapterError('xhs_cache_unavailable') from None


class XhsClient:
    """Only login inspection, note search and detail are permitted operations."""
    def __init__(self, profile, *, transport=None):
        if not isinstance(profile, XhsProfile): raise ValueError('XhsProfile required')
        self.profile, self.cache = profile, _Cache(profile)
        self._transport = transport or self._request
        self._checked_request = None

    def _request(self, method, path, payload, *, context):
        if (method,path) not in {('GET','/api/v1/login/status'),('POST','/api/v1/feeds/search'),('POST','/api/v1/feeds/detail')}:
            raise DataAdapterError('unsupported')
        context.begin_operation()
        connection = http.client.HTTPConnection('127.0.0.1', urlsplit(self.profile.base_url).port,
            timeout=min(90, context.remaining_seconds()))
        try:
            body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
            connection.request(method, path, body=body, headers={'Authorization':'Bearer '+self.profile.token,
                'Content-Type':'application/json', 'Accept':'application/json'})
            reply = connection.getresponse()
            if reply.status in {401,403}: raise DataAdapterError('authentication_failed')
            if reply.status == 429: raise DataAdapterError('rate_limit')
            if 300 <= reply.status < 400: raise DataAdapterError('blocked_url')
            if reply.getheader('Content-Encoding', 'identity').lower() not in {'', 'identity'}:
                raise DataAdapterError('upstream_schema')
            if reply.getheader('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
                raise DataAdapterError('upstream_schema')
            length = reply.getheader('Content-Length')
            if length is not None:
                if not re.fullmatch(r'[0-9]{1,12}', length): raise DataAdapterError('upstream_schema')
                if int(length) > MAX_BYTES: raise DataAdapterError('response_too_large')
            raw = bytearray()
            while True:
                context.check_active()
                if connection.sock: connection.sock.settimeout(min(90, context.remaining_seconds()))
                part = reply.read(min(65536, MAX_BYTES+1-len(raw)))
                raw.extend(part)
                if len(raw)>MAX_BYTES: raise DataAdapterError('response_too_large')
                if not part: break
            context.check_active()
            try: result = json.loads(raw)
            except (ValueError, UnicodeError): raise DataAdapterError('upstream_schema') from None
            if not isinstance(result,dict): raise DataAdapterError('upstream_schema')
            if reply.status != 200 or result.get('success') is not True:
                # Inspect locally for classification only. Never expose raw errors/URLs.
                message = str(result.get('details',''))+str(result.get('error',''))
                if any(s in message.lower() for s in ('captcha','verify','验证','安全限制','风控')): raise DataAdapterError('web_content_challenge')
                if any(s in message.lower() for s in ('not logged','登录','login')): raise DataAdapterError('xhs_login_required')
                if any(s in message for s in ('笔记不存在','已删除','无法访问')): raise DataAdapterError('not_found')
                if any(s in message.lower() for s in ('deadline exceeded','timeout','timed out','超时')): raise DataAdapterError('timeout')
                raise DataAdapterError('xhs_backend_error')
            if not isinstance(result.get('data'),dict): raise DataAdapterError('upstream_schema')
            return result['data']
        except (socket.timeout, TimeoutError): raise DataAdapterError('timeout') from None
        except (OSError, http.client.HTTPException): raise DataAdapterError('xhs_backend_unavailable') from None
        finally: connection.close()

    def _call(self, method, path, payload, context):
        self.cache._ensure()
        try:
            with _directory_lock(self.cache.root, context):
                return self._transport(method,path,payload,context=context)
        except OSError: raise DataAdapterError('xhs_cache_unavailable') from None

    def check_login(self, *, context):
        """Explicit live login probe, never called by local configuration health."""
        context.check_active()
        data = self._call('GET','/api/v1/login/status',None,context)
        context.check_active()
        if not isinstance(data,dict) or type(data.get('is_logged_in')) is not bool: raise DataAdapterError('upstream_schema')
        if not data['is_logged_in']: raise DataAdapterError('xhs_login_required')
        self._checked_request = context.request_id
        return {'logged_in':True}

    def _login(self, context):
        if self._checked_request != context.request_id: self.check_login(context=context)

    def search(self, query, *, sort='latest', context, refresh=False, snapshot=None):
        """One bounded upstream search snapshot; no invented upstream page numbers."""
        if (not isinstance(query,str) or not query.strip() or len(query)>200 or any(ord(c)<32 for c in query)
                or sort not in SORTS or type(refresh) is not bool): raise ValueError('Invalid XHS search')
        key = 'search:'+sort+':'+query
        self._login(context)
        cached = self.cache.get(key, 300)
        if snapshot:
            if not cached or _snapshot(cached) != snapshot: raise DataAdapterError('material_cursor_stale')
            return cached
        if cached and not refresh: return cached
        data = self._call('POST','/api/v1/feeds/search',{'keyword':query,'filters':{'sort_by':SORTS[sort]}},context)
        context.check_active()
        rows = data.get('feeds') if isinstance(data,dict) else None
        if not isinstance(rows,list) or len(rows)>200 or any(not isinstance(r,dict) for r in rows): raise DataAdapterError('upstream_schema')
        reply = XhsResponse({'feeds':rows}, datetime.now(timezone.utc))
        for row in rows:
            if row.get('modelType') == 'note':
                try: identifier = _id(row.get('id')); access = _token(row.get('xsecToken'))
                except DataAdapterError: continue
                self.cache.put('access:'+identifier, XhsResponse({'token':access},reply.fetched_at))
        self.cache.put(key, reply)
        return reply

    def detail(self, identifier, *, context, comment_limit=0, refresh=False):
        """Read a discovered note; comments are bounded independently from body text."""
        identifier = _id(identifier)
        if type(comment_limit) is not int or not 0 <= comment_limit <= 19 or type(refresh) is not bool: raise ValueError('Invalid XHS detail options')
        self._login(context)
        key='detail:'+identifier+':'+str(comment_limit)
        cached = self.cache.get(key, 3600)
        if cached and not refresh: return cached
        access = self.cache.get('access:'+identifier, 86400)
        if not access: raise DataAdapterError('xhs_reference_unavailable')
        payload={'feed_id':identifier,'xsec_token':_token(access.data.get('token')),
            'load_all_comments':bool(comment_limit), 'comment_config':{'click_more_replies':False,
                'max_replies_threshold':1,'max_comment_items':max(1,comment_limit),'scroll_speed':'normal'}}
        data=self._call('POST','/api/v1/feeds/detail',payload,context)
        context.check_active()
        if (not isinstance(data,dict) or data.get('feed_id')!=identifier or not isinstance(data.get('data'),dict)
                or not isinstance(data['data'].get('note'),dict) or data['data']['note'].get('noteId')!=identifier):
            raise DataAdapterError('upstream_schema')
        reply=XhsResponse(data['data'],datetime.now(timezone.utc))
        self.cache.put(key,reply)
        return reply


def _snapshot(reply):
    # Fingerprint excludes xsec tokens: cursors cannot be used as note credentials.
    rows=[{'id':r.get('id'),'type':r.get('modelType'),'title':(r.get('noteCard') or {}).get('displayTitle') if isinstance(r.get('noteCard'),dict) else None}
          for r in reply.data['feeds']]
    return hashlib.sha256(json.dumps([reply.fetched_at.isoformat(),rows],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
