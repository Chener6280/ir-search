"""Fiona-only Bearer transport and bounded read-only MCP client.

The normalized adapter lives in ir_search.adapters.fiona. This transport preserves
vendor fields and quality flags without changing their units.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import http.client
import json
import re
import socket
import ssl
import time
from threading import Event, Thread, Lock

from .credentials import SourceConfigError, read_credentials
from .mcp_protocol import _decode, _response_for
from ir_search.context import RequestStopped
from ir_search.registry import DataAdapterError

_HOST = 'track.finoview.com.cn'
_MAX_BYTES = 4 * 1024 * 1024
ROUTE_TOOLS = {
    'futures_market_data': frozenset({
        'get_futures_trading_calendar', 'get_latest_futures_trading_date', 'get_next_futures_trading_date',
        'get_futures_product_info', 'get_tradable_futures_contracts', 'get_futures_contract_info_table',
        'get_futures_contract_trading_params', 'get_futures_snapshot_quotes', 'get_futures_timeseries_quotes',
        'get_futures_dominant_quotes', 'get_futures_member_company_names', 'get_futures_member_positions',
        'get_futures_volatility', 'get_futures_warehouse_quantity', 'get_futures_warehouse_distribution'}),
    'options_market_data': frozenset({
        'select_option_contracts', 'get_option_contract_info', 'get_option_contract_attributes',
        'get_option_active_expiry_month', 'get_option_snapshot_quotes', 'get_option_history_quotes',
        'get_option_risk_metrics', 'get_option_indicators', 'get_option_member_rank',
        'get_option_volume_open_interest_ratio', 'get_option_call_put_ratio'}),
}
_LOCK = Lock()
_NEXT = {}


@dataclass(frozen=True)
class FionaProfile:
    token: str = field(repr=False)
    max_calls_per_query: int = 20

    def __post_init__(self):
        if (not isinstance(self.token, str) or not re.fullmatch(r'[A-Za-z0-9_.~+/=-]{8,4096}', self.token)
                or self.token.lower() in {'your_token', 'placeholder', 'changeme'}):
            raise SourceConfigError('source_credentials_missing')
        if type(self.max_calls_per_query) is not int or not 1 <= self.max_calls_per_query <= 40:
            raise SourceConfigError()


def fiona_profile(*, values=None, env_file=None):
    """Load only the Fiona-specific credential; never reuse a REST/vendor key."""
    values = read_credentials(env_file) if values is None else values
    enabled = values.get('FIONA_MCP_ENABLED', 'false').lower()
    if enabled not in {'true', 'false'}: raise SourceConfigError()
    if enabled == 'false': return None
    tokens = {v.removeprefix('Bearer ').strip() for v in (values.get('FIONA_MCP_TOKEN'), values.get('FIONA_MCP')) if v}
    if len(tokens) > 1: raise SourceConfigError('conflicting_fiona_credentials')
    try:
        return FionaProfile(next(iter(tokens), ''), int(values.get('FIONA_MAX_CALLS_PER_QUERY', '20')))
    except (ValueError, TypeError) as exc:
        if isinstance(exc, SourceConfigError): raise
        raise SourceConfigError() from None


def _throttle(route, context):
    # Conservative per-process route-wide spacing, including MCP handshake calls.
    while True:
        context.check_active()
        with _LOCK:
            delay = _NEXT.get(route, 0) - time.monotonic()
            if delay <= 0:
                _NEXT[route] = time.monotonic() + 1.1
                return
        context._cancelled.wait(min(delay, context.remaining_seconds(), 0.05))


@dataclass(frozen=True)
class _RPCResponse:
    payload: dict = field(repr=False)
    session_id: str = field(default="", repr=False)

def _error_code(status=200, message=""):
    message = str(message)[:4000].lower()
    if status == 501 or re.search(r"(?:server error|status[^0-9]*|code[^0-9]*)['\s:]*501\b", message):
        return 'unsupported'
    if re.search(r"server error\s*['\"]?(?:500|502|503|504)\b", message):
        return 'network'
    if 'timed out' in message or 'readtimeout' in message: return 'timeout'
    if status == 401 or any(s in message for s in ("unauthorized", "not logged", "token expired", "invalid token", "invalid api key", "api key expired", "未登录")):
        return "authentication_failed"
    if status in {402,403} or any(s in message for s in ("权限", "permission", "not allowed", "forbidden")):
        return "entitlement_denied"
    if any(s in message for s in ("quota", "余额不足", "配额", "额度不足")): return "quota"
    if status == 429 or any(s in message for s in ("429", "频繁", "限流", "rate limit")):
        return "rate_limit"
    if status == 404: return "not_found"
    return "network" if status >= 500 else "upstream_schema"


def _post(profile, route, payload, *, session_id, version, context):
    if route not in ROUTE_TOOLS: raise DataAdapterError("unsupported")
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
                    if sock is not None:
                        try:
                            sock.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                    return

        watcher = Thread(target=stop_socket, daemon=True)
        watcher.start()
        connection.connect()
        active_socket = connection.sock
        context.check_active()
        active_socket.settimeout(context.remaining_seconds())
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "Accept-Encoding": "identity", "User-Agent": "ir-search/0.1", "Authorization": "Bearer " + profile.token}
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        if version:
            headers["MCP-Protocol-Version"] = version
        connection.request("POST", "/fiona/mcp/" + route,
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
        if profile.token.encode() in raw:
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


@dataclass(frozen=True)
class FionaResponse:
    result: dict = field(repr=False)
    fetched_at: datetime
    route: str
    tool: str
    source_text_trust: str = 'untrusted'


class FionaClient:
    """One route/session, only documented data reads; no write or research tools."""
    def __init__(self, profile: FionaProfile, route: str, *, transport=None):
        if not isinstance(profile, FionaProfile) or route not in ROUTE_TOOLS:
            raise ValueError('Fiona profile and supported data route required')
        self._profile, self.route, self._transport = profile, route, transport or _post
        self._session, self._version, self._ready = '', '', False
        self.http_requests, self.tool_calls = 0, 0
        self.server_info = {}

    def _rpc(self, method, params, context, notification=False):
        if context.account_scope != 'default': raise DataAdapterError('entitlement_denied')
        if self._transport is _post: _throttle(self.route, context)
        context.begin_operation()
        self.http_requests += 1
        payload = {'jsonrpc': '2.0', 'method': method, 'params': params}
        if not notification: payload['id'] = self.http_requests
        reply = self._transport(self._profile, self.route, payload,
            session_id=self._session, version=self._version, context=context)
        context.check_active()
        if not isinstance(reply, _RPCResponse): raise DataAdapterError('upstream_schema')
        if reply.session_id:
            if self._session and reply.session_id != self._session: raise DataAdapterError('upstream_schema')
            self._session = reply.session_id
        if notification: return {}
        response = _response_for(reply.payload, payload['id'])
        if response is None: raise DataAdapterError('upstream_schema')
        if 'error' in response: raise DataAdapterError(_error_code(message=response['error']))
        result = response.get('result')
        if not isinstance(result, dict) or self._profile.token in json.dumps(result, ensure_ascii=False):
            raise DataAdapterError('upstream_schema')
        return result

    def _initialize(self, context):
        if self._ready: return
        result = self._rpc('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
            'clientInfo': {'name': 'ir-search', 'version': '0.1'}}, context)
        if result.get('protocolVersion') not in {'2024-11-05', '2025-03-26', '2025-06-18'}:
            raise DataAdapterError('unsupported')
        self._version = result['protocolVersion']
        info = result.get('serverInfo', {})
        if not isinstance(info, dict): raise DataAdapterError('upstream_schema')
        self.server_info = {k: str(info[k])[:100] for k in ('name', 'version') if k in info}
        self._rpc('notifications/initialized', {}, context, notification=True)
        self._ready = True

    def list_tools(self, *, context) -> dict:
        """Discover one bounded tool page; expose continuation instead of guessing completeness."""
        self._initialize(context)
        result = self._rpc('tools/list', {}, context)
        rows = result.get('tools')
        if (not isinstance(rows, list) or len(rows) > 100 or any(not isinstance(r, dict)
                or not isinstance(r.get('name'), str) or not isinstance(r.get('inputSchema'), dict) for r in rows)):
            raise DataAdapterError('upstream_schema')
        return result

    def read(self, tool: str, arguments: dict, *, context) -> FionaResponse:
        """Return vendor MCP data and quality flags unchanged; not normalized market rows."""
        if (tool not in ROUTE_TOOLS[self.route] or not isinstance(arguments, dict)
                or len(arguments) > 30): raise DataAdapterError('unsupported')
        try:
            encoded = json.dumps(arguments, ensure_ascii=False, allow_nan=False)
        except (ValueError, TypeError): raise DataAdapterError('unsupported') from None
        if len(encoded) > 8192 or self._profile.token in encoded: raise DataAdapterError('unsupported')
        if self.tool_calls >= self._profile.max_calls_per_query:
            raise DataAdapterError('fiona_call_budget_exhausted')
        self._initialize(context)
        self.tool_calls += 1
        result = self._rpc('tools/call', {'name': tool, 'arguments': arguments}, context)
        if result.get('isError') is True:
            raise DataAdapterError(_error_code(message=result.get('content')))
        if result.get('isError', False) is not False or not isinstance(result.get('content'), list):
            raise DataAdapterError('upstream_schema')
        return FionaResponse(result, datetime.now(timezone.utc), self.route, tool)
