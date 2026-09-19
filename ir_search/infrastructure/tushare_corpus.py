"""Bounded official-host MCP POST client for three read-only corpus tools.

Implements the exercised Streamable HTTP subset (JSON/SSE responses); no arbitrary
tool proxy, discovery, external redirects, server-request execution or retry loop.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import http.client
import json
import re
import socket
import ssl
from threading import Event, Thread
from urllib.parse import urlencode

from .credentials import TushareCorpusProfile
from ir_search.context import RequestStopped
from ir_search.registry import DataAdapterError

_HOST = "api.tushare.pro"
_MAX_BYTES = 16 * 1024 * 1024
_MAX_ROWS = 1000
FIELDS = {
    "research_report": ("trade_date", "title", "report_type", "author", "name", "ts_code", "inst_csname", "ind_name", "url", "abstr", "report_code"),
    "major_news": ("title", "pub_time", "src", "url", "content"),
    "npr": ("pubtime", "title", "pcode", "puborg", "ptype", "url", "content_html"),
}
# Documentation limits are coverage hints, not a strict wire schema: live news
# has returned 629 rows despite the documented 400. Retain a separate hard cap.
ROW_LIMITS = {"research_report": 1000, "major_news": 400, "npr": 500}


@dataclass(frozen=True)
class CorpusResponse:
    rows: list[dict] = field(repr=False)
    fetched_at: datetime


@dataclass(frozen=True)
class _RPCResponse:
    payload: dict = field(repr=False)
    session_id: str = field(default="", repr=False)


def _error_code(status=200, message=""):
    if status == 401:
        return "authentication_failed"
    if status in {402, 403}:
        return "entitlement_denied"
    if status == 429:
        return "rate_limit"
    message = str(message)[:4000].lower()
    if any(v in message for v in ("每天", "每日", "quota", "daily limit")):
        return "quota"
    if any(v in message for v in ("频率", "限流", "每分钟", "rate limit")):
        return "rate_limit"
    if any(v in message for v in ("权限", "无权", "permission", "subscription")):
        return "entitlement_denied"
    if "token" in message and any(v in message for v in ("invalid", "无效", "不对", "错误", "expired")):
        return "authentication_failed"
    return "network" if status >= 500 else "upstream_schema"


from .mcp_protocol import _decode, _response_for


def _post(profile, payload, *, session_id, version, context):
    connection, active_socket, watcher = None, None, None
    finished = Event()
    try:
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
                   "Accept-Encoding": "identity", "User-Agent": "ir-search/0.1"}
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        if version:
            headers["MCP-Protocol-Version"] = version
        connection.request("POST", "/mcp/?" + urlencode({"token": profile.token}),
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
            raise DataAdapterError("tushare_response_too_large" if length.isdigit() else "upstream_schema")
        raw, pending, event_lines = bytearray(), bytearray(), []
        result = None
        while True:
            context.check_active()
            active_socket.settimeout(context.remaining_seconds())
            chunk = response.read1(min(65536, _MAX_BYTES + 1 - len(raw)))
            raw.extend(chunk)
            if len(raw) > _MAX_BYTES:
                raise DataAdapterError("tushare_response_too_large")
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


def _validate_call(tool, arguments):
    if tool not in FIELDS or not isinstance(arguments, dict):
        raise DataAdapterError("unsupported")
    allowed = {"start_date", "end_date", "fields"} | ({"ts_code"} if tool == "research_report" else {"src"} if tool == "major_news" else set())
    if set(arguments) - allowed or not {"start_date", "end_date", "fields"} <= set(arguments):
        raise DataAdapterError("unsupported")
    fields = arguments["fields"]
    if (not isinstance(fields, list) or not fields or any(not isinstance(f, str) or f not in FIELDS[tool] for f in fields)
            or len(set(fields)) != len(fields)):
        raise DataAdapterError("unsupported")
    report = tool == "research_report"
    fmt, pattern = ("%Y%m%d", r"\d{8}") if report else ("%Y-%m-%d %H:%M:%S", r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
    try:
        values = [arguments[k] for k in ("start_date", "end_date")]
        if any(not isinstance(v, str) or not re.fullmatch(pattern, v) for v in values):
            raise ValueError()
        start, end = (datetime.strptime(v, fmt) for v in values)
        if start > end or (end - start).total_seconds() > (30 * 86400 if report else 86399):
            raise ValueError()
    except (ValueError, TypeError):
        raise DataAdapterError("unsupported") from None
    if "ts_code" in arguments and not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", str(arguments["ts_code"])):
        raise DataAdapterError("unsupported")
    if tool == "major_news" and arguments.get("src") not in {"新华网", "凤凰财经", "同花顺", "新浪财经", "华尔街见闻", "中证网", "财新网", "第一财经", "财联社"}:
        raise DataAdapterError("unsupported")


class TushareCorpusClient:
    """One search's bounded MCP session; no global tokens, cache or automatic retries."""

    def __init__(self, profile: TushareCorpusProfile, *, transport=None):
        if not isinstance(profile, TushareCorpusProfile):
            raise ValueError("TushareCorpusProfile required")
        self._profile, self._transport = profile, transport or _post
        self._session, self._version = "", ""
        self._ready = False
        self.http_requests, self.tool_calls = 0, 0

    def _rpc(self, method, params, context, *, notification=False):
        context.begin_operation()
        self.http_requests += 1
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            payload["id"] = self.http_requests
        reply = self._transport(self._profile, payload, session_id=self._session, version=self._version, context=context)
        context.check_active()
        if not isinstance(reply, _RPCResponse):
            raise DataAdapterError("upstream_schema")
        if reply.session_id:
            if self._session and reply.session_id != self._session:
                raise DataAdapterError("upstream_schema")
            self._session = reply.session_id
        if notification:
            return {}
        response = reply.payload
        if _response_for(response, payload["id"]) is None:
            raise DataAdapterError("upstream_schema")
        if "error" in response:
            raise DataAdapterError(_error_code(message=response["error"]))
        result = response.get("result")
        if not isinstance(result, dict):
            raise DataAdapterError("upstream_schema")
        if self._profile.token in json.dumps(result, ensure_ascii=False):
            raise DataAdapterError("upstream_schema")
        return result

    def fetch(self, tool: str, arguments: dict, *, context) -> CorpusResponse:
        """Call only the three declared corpus tools with validated bounded parameters."""
        _validate_call(tool, arguments)
        context.check_active()
        if self.tool_calls >= self._profile.max_calls_per_query:
            raise DataAdapterError("tushare_call_budget_exhausted")
        self.tool_calls += 1
        if not self._ready:
            result = self._rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                "clientInfo": {"name": "ir-search", "version": "0.1"}}, context)
            if result.get("protocolVersion") != "2025-03-26":
                raise DataAdapterError("unsupported")
            self._version = result["protocolVersion"]
            self._rpc("notifications/initialized", {}, context, notification=True)
            self._ready = True
        result = self._rpc("tools/call", {"name": tool, "arguments": arguments}, context)
        blocks = result.get("content")
        if result.get("isError") is True:
            raise DataAdapterError(_error_code(message=blocks))
        if result.get("isError", False) is not False or not isinstance(blocks, list) or len(blocks) != 1:
            raise DataAdapterError("upstream_schema")
        block = blocks[0]
        if not isinstance(block, dict) or block.get("type") != "text" or not isinstance(block.get("text"), str):
            raise DataAdapterError("upstream_schema")
        try:
            rows = _decode(block["text"])
        except (ValueError, RecursionError):
            raise DataAdapterError("upstream_schema") from None
        if not isinstance(rows, list):
            raise DataAdapterError(_error_code(message=rows))
        if len(rows) > _MAX_ROWS:
            raise DataAdapterError("tushare_response_too_many_rows")
        if any(not isinstance(row, dict) for row in rows):
            raise DataAdapterError("upstream_schema")
        if self._profile.token in json.dumps(rows, ensure_ascii=False):
            raise DataAdapterError("upstream_schema")
        return CorpusResponse(rows, datetime.now(timezone.utc))
