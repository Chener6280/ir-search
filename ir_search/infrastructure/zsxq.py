"""Read-only official Knowledge Planet MCP client; no CLI or browser session."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import http.client
import json
import re
import socket
import ssl
from threading import Event, Thread

from .credentials import ZsxqProfile
from .mcp_protocol import _decode, _response_for
from ir_search.context import RequestStopped
from ir_search.registry import DataAdapterError

_HOST = "mcp.zsxq.com"
_MAX_BYTES = 4 * 1024 * 1024

@dataclass(frozen=True)
class _RPCResponse:
    payload: dict = field(repr=False)
    session_id: str = field(default="", repr=False)

@dataclass(frozen=True)
class ZsxqResponse:
    data: dict = field(repr=False)
    fetched_at: datetime


def _error_code(status=200, message=""):
    message = str(message)[:4000].lower()
    if status == 401 or any(s in message for s in ("unauthorized", "not logged", "token expired", "invalid token", "未登录")):
        return "authentication_failed"
    if status in {402,403} or any(s in message for s in ("权限", "permission", "not allowed", "forbidden")):
        return "entitlement_denied"
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
        connection.request("POST", "/topic/",
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


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,29}", value):
        raise DataAdapterError("unsupported")
    return value


def _time(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:?\d\d)", value):
        raise DataAdapterError("unsupported")
    try:
        normalized = re.sub(r"([+-]\d\d)(\d\d)$", r"\1:\2", value.replace("Z", "+00:00"))
        parsed = datetime.fromisoformat(normalized)
        if parsed.utcoffset() is None: raise ValueError()
        return parsed
    except ValueError:
        raise DataAdapterError("unsupported") from None


def _validate_call(tool, arguments):
    fields = {
        "get_self_info": (set(), set()),
        "get_user_groups": ({"user_id", "limit", "scope"}, {"user_id", "limit", "scope"}),
        "get_group_topics": ({"group_id", "limit", "scope", "end_time"}, {"group_id", "limit", "scope"}),
        "get_topic_info": ({"topic_id"}, {"topic_id"}),
        "get_topic_comments": ({"topic_id", "limit", "index"}, {"topic_id", "limit"}),
        "call_zsxq_api": ({"method", "path"}, {"method", "path"}),
    }
    if tool not in fields or not isinstance(arguments, dict): raise DataAdapterError("unsupported")
    allowed, required = fields[tool]
    if set(arguments) - allowed or not required <= set(arguments): raise DataAdapterError("unsupported")
    for key in ("user_id", "group_id", "topic_id"):
        if key in arguments: _id(arguments[key])
    if "limit" in arguments:
        maximum = 200 if tool == "get_user_groups" else 30
        if type(arguments["limit"]) is not int or not 1 <= arguments["limit"] <= maximum:
            raise DataAdapterError("unsupported")
    if "scope" in arguments and arguments["scope"] != ("normal" if tool == "get_user_groups" else "all"):
        raise DataAdapterError("unsupported")
    if "end_time" in arguments: _time(arguments["end_time"])
    if "index" in arguments and (not isinstance(arguments["index"], str) or not re.fullmatch(r"[A-Za-z0-9_.:+/=-]{1,512}", arguments["index"])):
        raise DataAdapterError("unsupported")
    if tool == "call_zsxq_api" and (arguments["method"] != "GET" or not isinstance(arguments["path"], str)
            or not re.fullmatch(r"/v2/files/[1-9][0-9]{0,29}/download_url", arguments["path"])):
        raise DataAdapterError("unsupported")


class ZsxqClient:
    """Request-local official session with a fixed read-only tool/argument allowlist."""
    def __init__(self, profile: ZsxqProfile, *, transport=None):
        if not isinstance(profile, ZsxqProfile): raise ValueError("ZsxqProfile required")
        self._profile, self._transport = profile, transport or _post
        self._session, self._version, self._ready = "", "", False
        self.http_requests = 0

    def _rpc(self, method, params, context, notification=False):
        context.begin_operation()
        self.http_requests += 1
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification: payload["id"] = self.http_requests
        reply = self._transport(self._profile, payload, session_id=self._session, version=self._version, context=context)
        context.check_active()
        if not isinstance(reply, _RPCResponse): raise DataAdapterError("upstream_schema")
        if reply.session_id:
            if self._session and self._session != reply.session_id: raise DataAdapterError("upstream_schema")
            self._session = reply.session_id
        if notification: return {}
        response = _response_for(reply.payload, payload["id"])
        if response is None: raise DataAdapterError("upstream_schema")
        if "error" in response: raise DataAdapterError(_error_code(message=response["error"]))
        result = response.get("result")
        if not isinstance(result, dict) or self._profile.token in json.dumps(result, ensure_ascii=False):
            raise DataAdapterError("upstream_schema")
        return result

    def read(self, tool: str, arguments: dict, *, context) -> ZsxqResponse:
        """Invoke one validated read; generated search and every write are excluded."""
        _validate_call(tool, arguments)
        context.check_active()
        if not self._ready:
            result = self._rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "ir-search", "version": "0.1"}}, context)
            if result.get("protocolVersion") != "2024-11-05": raise DataAdapterError("unsupported")
            self._version = result["protocolVersion"]
            self._rpc("notifications/initialized", {}, context, notification=True)
            self._ready = True
        result = self._rpc("tools/call", {"name": tool, "arguments": arguments}, context)
        if result.get("isError") is True: raise DataAdapterError(_error_code(message=result.get("content")))
        blocks = result.get("content")
        if result.get("isError", False) is not False or not isinstance(blocks, list) or len(blocks) != 1:
            raise DataAdapterError("upstream_schema")
        block = blocks[0]
        if not isinstance(block, dict) or block.get("type") != "text" or not isinstance(block.get("text"), str):
            raise DataAdapterError("upstream_schema")
        try: data = _decode(block["text"])
        except (ValueError, RecursionError): raise DataAdapterError("upstream_schema") from None
        if not isinstance(data, dict): raise DataAdapterError("upstream_schema")
        if data.get("success") is not True:
            raise DataAdapterError(_error_code(message=data))
        return ZsxqResponse(data, datetime.now(timezone.utc))

    def groups(self, *, context) -> ZsxqResponse:
        """Read one directory page, without assuming its count proves complete membership."""
        user = self.read("get_self_info", {}, context=context).data.get("user")
        if not isinstance(user, dict): raise DataAdapterError("upstream_schema")
        response = self.read("get_user_groups", {"user_id": _id(str(user.get("user_id", ""))), "limit": 200, "scope": "normal"}, context=context)
        rows = response.data.get("groups")
        if not isinstance(rows, list) or len(rows) > 200 or any(not isinstance(r, dict) for r in rows):
            raise DataAdapterError("upstream_schema")
        return response
