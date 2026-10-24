"""Bounded official Wisburg MCP transport; no redirects, arbitrary tools or generation."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
import http.client
import json
import re
import socket
import ssl
from threading import Event, Thread

from ._interrupt import wake_blocked_socket
from .credentials import WisburgProfile
from .mcp_protocol import _decode, _response_for
from ir_search.context import RequestStopped
from ir_search.registry import DataAdapterError

_HOST = "mcp.wisburg.com"
_MAX_BYTES = 4 * 1024 * 1024

@dataclass(frozen=True)
class _RPCResponse:
    payload: dict = field(repr=False)
    session_id: str = field(default="", repr=False)

@dataclass(frozen=True)
class WisburgResponse:
    text: str = field(repr=False)
    fetched_at: datetime


def _error_code(status=200, message=""):
    message = str(message)[:4000].lower()
    if status == 401 or any(s in message for s in ("unauthorized", "not logged", "token expired", "invalid token", "invalid api key", "api key expired", "未登录")):
        return "authentication_failed"
    if status in {402,403} or any(s in message for s in ("权限", "permission", "not allowed", "forbidden")):
        return "entitlement_denied"
    if any(s in message for s in ("quota", "余额不足", "配额", "额度不足")): return "quota"
    if status == 429 or any(s in message for s in ("429", "频繁", "限流", "rate limit")):
        return "rate_limit"
    if status == 404: return "not_found"
    return "network" if status >= 500 else "upstream_schema"


def _post(profile, payload, *, session_id, version, context):
    connection, active_socket, watcher = None, None, None
    finished = Event()
    try:
        context.check_active()
        tls = ssl.create_default_context()
        tls.minimum_version = ssl.TLSVersion.TLSv1_2
        connection = http.client.HTTPSConnection(_HOST, timeout=context.remaining_seconds(), context=tls)

        def stop_socket():
            while not finished.wait(0.05):
                try:
                    context.check_active()
                except RequestStopped:
                    sock = active_socket or connection.sock
                    wake_blocked_socket(sock)
                    return

        watcher = Thread(target=stop_socket, daemon=True)
        watcher.start()
        connection.connect()
        active_socket = connection.sock
        context.check_active()
        active_socket.settimeout(context.remaining_seconds())
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "Accept-Encoding": "identity", "User-Agent": "ir-search/0.1", "Authorization": "Bearer " + profile.api_key}
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        if version:
            headers["MCP-Protocol-Version"] = version
        connection.request("POST", "/mcp",
                           body=json.dumps(payload).encode(), headers=headers)
        response = connection.getresponse()
        if 300 <= response.status < 400:
            raise DataAdapterError("entitlement_denied")
        if response.status >= 400:
            raise DataAdapterError(_error_code(response.status))
        returned_session = response.getheader("Mcp-Session-Id", "")
        if returned_session and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", returned_session):
            raise DataAdapterError("upstream_schema")
        if "id" not in payload and response.status in {200, 202, 204}:
            return _RPCResponse({}, returned_session)
        if response.status != 200:
            raise DataAdapterError("upstream_schema")
        content_type = response.getheader("Content-Type", "").split(";")[0].strip().lower()
        if content_type not in {"application/json", "text/event-stream"}:
            raise DataAdapterError("upstream_schema")
        if response.getheader("Content-Encoding", "identity").lower() != "identity":
            raise DataAdapterError("upstream_schema")
        length = response.getheader("Content-Length")
        if length is not None and (not length.isdigit() or int(length) > _MAX_BYTES):
            raise DataAdapterError("response_too_large" if length.isdigit() else "upstream_schema")
        raw, pending, event_lines = bytearray(), bytearray(), []
        result = None
        while True:
            context.check_active()
            active_socket.settimeout(context.remaining_seconds())
            chunk = response.read1(min(65536, _MAX_BYTES + 1 - len(raw)))
            raw.extend(chunk)
            if len(raw) > _MAX_BYTES:
                raise DataAdapterError("response_too_large")
            if content_type == "text/event-stream":
                pending.extend(chunk)
                if not chunk and pending:
                    pending.extend(b"\n\n")
                while b"\n" in pending:
                    line, _, pending = pending.partition(b"\n")
                    line = line.rstrip(b"\r")
                    if line.startswith(b"data:"):
                        value = line[5:]
                        event_lines.append(value[1:] if value.startswith(b" ") else value)
                    elif not line and event_lines:
                        result = _response_for(_decode(b"\n".join(event_lines)), payload["id"])
                        event_lines = []
                        if result is not None:
                            break
                if result is not None:
                    break
            if not chunk:
                break
        context.check_active()
        # An upstream echo must never put this query's credential into returned source text.
        if profile.api_key.encode() in raw:
            raise DataAdapterError("upstream_schema")
        if content_type == "application/json":
            result = _response_for(_decode(raw), payload["id"])
        if result is None:
            raise DataAdapterError("upstream_schema")
        return _RPCResponse(result, returned_session)
    except (RequestStopped, DataAdapterError):
        raise
    except ssl.SSLError:
        context.check_active()
        raise DataAdapterError("tls_error") from None
    except (TimeoutError, socket.timeout):
        context.check_active()
        raise DataAdapterError("timeout") from None
    except (ValueError, UnicodeError, RecursionError):
        raise DataAdapterError("upstream_schema") from None
    except Exception:
        context.check_active()
        raise DataAdapterError("network") from None
    finally:
        finished.set()
        if connection is not None:
            connection.close()
        if watcher is not None:
            watcher.join(timeout=0.2)


# Tool names and formats verified against the official MCP v0.8.4 directory.
LIST_TOOLS = {
    'ib': 'list-institutional-reports', 'company': 'list-company-reports',
    'am': 'list-am-reports', 'archive': 'list-archive-reports',
    'ec': 'list-earning-calls', 'feed': 'list-feed', 'market_daily': 'list-market-daily',
    'article': 'list-articles', 'mikko': 'list-mikko-logs',
}
DETAIL_TOOLS = {'report': 'get-report-detail', 'article': 'get-article-detail', 'mikko': 'get-mikko-log-detail'}
_EMPTY_LABELS = {'ib':'institutional reports', 'company':'company reports', 'am':'asset management reports',
                 'archive':'public institutional documents', 'ec':'earning call transcripts',
                 'feed':'feed items', 'market_daily':'market daily reports', 'article':'articles', 'mikko':'Mikko logs'}


def _identifier(value):
    if type(value) is not int or not 1 <= value <= 9007199254740991:
        raise DataAdapterError('unsupported')
    return value


def _time(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)', value):
        raise DataAdapterError('upstream_schema')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.utcoffset() is None: raise ValueError()
        return result.astimezone(timezone(timedelta(hours=8)))
    except ValueError: raise DataAdapterError('upstream_schema') from None


def _cursor(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_+=./:-]{1,512}', value):
        raise DataAdapterError('upstream_schema')
    return value


def _reference(kind, identifier):
    if kind not in DETAIL_TOOLS: raise DataAdapterError('unsupported')
    return 'wisburg://' + kind + '/' + str(_identifier(identifier))


def _parse_reference(value):
    if not isinstance(value, str): raise DataAdapterError('unsupported')
    match = re.fullmatch(r'wisburg://(report|article|mikko)/([1-9][0-9]{0,15})', value)
    if not match: raise DataAdapterError('unsupported')
    return match[1], _identifier(int(match[2]))


def _validate_call(tool, arguments):
    if not isinstance(arguments, dict): raise DataAdapterError('unsupported')
    if tool in LIST_TOOLS.values():
        if set(arguments) - {'first','after','query','startTime','endTime'} or not {'first','startTime','endTime'} <= set(arguments):
            raise DataAdapterError('unsupported')
        if type(arguments['first']) is not int or not 1 <= arguments['first'] <= 50: raise DataAdapterError('unsupported')
        if 'query' in arguments and (not isinstance(arguments['query'], str) or not 1 <= len(arguments['query']) <= 200
                or any(ord(c) < 32 for c in arguments['query'])): raise DataAdapterError('unsupported')
        start, end = _time(arguments['startTime']), _time(arguments['endTime'])
        if not 0 < (end-start).total_seconds() <= 367*86400: raise DataAdapterError('unsupported')
        if 'after' in arguments: _cursor(arguments['after'])
    elif tool in DETAIL_TOOLS.values():
        if set(arguments) != {'id'}: raise DataAdapterError('unsupported')
        _identifier(arguments['id'])
    else: raise DataAdapterError('unsupported')


class WisburgClient:
    """One request-local session; only stored content reads, never chat or generation."""
    def __init__(self, profile: WisburgProfile, *, transport=None):
        if not isinstance(profile, WisburgProfile): raise ValueError('WisburgProfile required')
        self._profile, self._transport = profile, transport or _post
        self._session, self._version, self._ready = '', '', False
        self.http_requests, self.tool_calls = 0, 0

    def _rpc(self, method, params, context, notification=False):
        context.begin_operation()
        self.http_requests += 1
        payload = {'jsonrpc':'2.0', 'method':method, 'params':params}
        if not notification: payload['id'] = self.http_requests
        reply = self._transport(self._profile, payload, session_id=self._session, version=self._version, context=context)
        context.check_active()
        if not isinstance(reply, _RPCResponse): raise DataAdapterError('upstream_schema')
        if reply.session_id:
            if self._session and self._session != reply.session_id: raise DataAdapterError('upstream_schema')
            self._session = reply.session_id
        if notification: return {}
        response = _response_for(reply.payload, payload['id'])
        if response is None: raise DataAdapterError('upstream_schema')
        if 'error' in response: raise DataAdapterError(_error_code(message=response['error']))
        result = response.get('result')
        if not isinstance(result, dict) or self._profile.api_key in json.dumps(result, ensure_ascii=False):
            raise DataAdapterError('upstream_schema')
        return result

    def read(self, tool: str, arguments: dict, *, context) -> WisburgResponse:
        """Read a bounded list or stored detail via the official TLS endpoint."""
        _validate_call(tool, arguments)
        context.check_active()
        if context.account_scope != 'default': raise DataAdapterError('entitlement_denied')
        if self.tool_calls >= self._profile.max_calls_per_query: raise DataAdapterError('wisburg_call_budget_exhausted')
        self.tool_calls += 1
        if not self._ready:
            result = self._rpc('initialize', {'protocolVersion':'2024-11-05', 'capabilities':{},
                'clientInfo':{'name':'ir-search','version':'0.1'}}, context)
            if result.get('protocolVersion') != '2024-11-05': raise DataAdapterError('unsupported')
            self._version = result['protocolVersion']
            self._rpc('notifications/initialized', {}, context, notification=True)
            self._ready = True
        result = self._rpc('tools/call', {'name':tool, 'arguments':arguments}, context)
        if result.get('isError') is True: raise DataAdapterError(_error_code(message=result.get('content')))
        blocks = result.get('content')
        if result.get('isError', False) is not False or not isinstance(blocks, list) or len(blocks) != 1:
            raise DataAdapterError('upstream_schema')
        block = blocks[0]
        if not isinstance(block, dict) or block.get('type') != 'text' or not isinstance(block.get('text'), str):
            raise DataAdapterError('upstream_schema')
        if len(block['text']) > 1000000: raise DataAdapterError('response_too_large')
        return WisburgResponse(block['text'], datetime.now(timezone.utc))


@dataclass(frozen=True)
class WisburgRecord:
    identifier: int
    title: str
    published_at: datetime
    text: str = field(repr=False, default='')


def _parse_list(text, category, *, first):
    """Parse the observed official text format; schema changes fail visibly."""
    if category not in LIST_TOOLS or not isinstance(text, str): raise DataAdapterError('upstream_schema')
    if text.strip() == 'No '+_EMPTY_LABELS[category]+' found matching the criteria.':
        return [], False, None
    header = re.match(r'Found ([0-9]+) (?:reports|feed items|market daily reports|articles|Mikko logs):\n\n', text)
    if not header or not 0 <= int(header[1]) <= first: raise DataAdapterError('upstream_schema')
    content = text[header.end():]
    cursor, more = None, None
    # The final footer belongs to protocol framing, never to a document body.
    body, sep, footer = content.rpartition('\n\n--- Page Info ---\n')
    if sep:
        match = re.fullmatch(r'Next cursor: ([^\n]+)\nUse "after" parameter with this value to get the next page\.?\s*', footer)
        if not match: raise DataAdapterError('upstream_schema')
        cursor, more, content = _cursor(match[1]), True, body
    starts = list(re.finditer(r'^\[([1-9][0-9]{0,15})\] ([^\n]+)\n', content+'\n', re.M))
    if len(starts) != int(header[1]) or (starts and starts[0].start() != 0): raise DataAdapterError('upstream_schema')
    rows=[]
    for i, match in enumerate(starts):
        identifier = _identifier(int(match[1]))
        segment = content[match.end():starts[i+1].start() if i+1<len(starts) else len(content)]
        if category == 'mikko':
            published = _time(match[2]); title = 'Mikko 日志 ' + match[2]
            body = re.sub(r'\n\n---\s*$', '', segment.rstrip()).strip()
        else:
            title = match[2].strip()
            date = re.match(r'  date: ([^\n]+)(?:\n|$)', segment)
            if not date: raise DataAdapterError('upstream_schema')
            published = _time(date[1]); body = segment[date.end():].strip()
        if not title or len(title)>1000: raise DataAdapterError('upstream_schema')
        rows.append(WisburgRecord(identifier,title,published,body))
    if len({r.identifier for r in rows}) != len(rows): raise DataAdapterError('upstream_schema')
    return rows, more, cursor


def _parse_detail(text, kind, identifier):
    if kind == 'mikko':
        pattern = r'- ID: ([1-9][0-9]*)\n- Date: ([^\n]+)\n\n## Content\n\n([\s\S]*)'
        match = re.fullmatch(pattern, text.strip())
        if not match: raise DataAdapterError('upstream_schema')
        found, date, body = match.groups(); title = 'Mikko 日志 ' + date
    else:
        label = 'Summary' if kind == 'report' else 'Content'
        pattern = r'# ([^\n]+)\n\n- ID: ([1-9][0-9]*)\n- Date: ([^\n]+)\n\n## '+label+r'\n\n([\s\S]*)'
        match = re.fullmatch(pattern, text.strip())
        if not match: raise DataAdapterError('upstream_schema')
        title, found, date, body = match.groups()
    if int(found) != identifier or not title.strip() or len(title)>1000: raise DataAdapterError('upstream_schema')
    if not body.strip() or body.strip() in {'(No summary available)', '(No content available)'}:
        raise DataAdapterError('no_extracted_text')
    return WisburgRecord(identifier,title,_time(date),body.strip())
