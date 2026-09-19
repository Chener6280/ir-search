"""Bounded HTTPS access to FMP stable endpoints. Never expose authenticated URLs."""
from __future__ import annotations

import copy
import http.client
import json
import re
import socket
import ssl
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from functools import lru_cache
from threading import Event, Lock, Thread
from urllib.parse import urlencode

from ._interrupt import wake_blocked_socket
from ir_search.context import RequestStopped
from ir_search.registry import DataAdapterError

_HOST = "financialmodelingprep.com"
_ENDPOINTS = {"profile", "historical-price-eod/light", "income-statement", "balance-sheet-statement", "cash-flow-statement"}
_MAX_BYTES = 1048576
_TICKER = re.compile(r"[A-Z][A-Z0-9.-]{0,19}")


@dataclass(frozen=True)
class _Response:
    records: list = field(repr=False)
    fetched_at: datetime
    cache_hit: bool = False


@dataclass
class _Budget:
    limit: int
    used: int = 0

    def consume(self):
        if self.used >= self.limit:
            raise DataAdapterError("fmp_request_budget_exceeded")
        self.used += 1


def _validate_params(endpoint, params):
    expected = {"symbol"} if endpoint == "profile" else {"symbol", "from", "to"} if endpoint == "historical-price-eod/light" else {"symbol", "period", "limit"}
    if (endpoint not in _ENDPOINTS or set(params) != expected
            or not isinstance(params.get("symbol"), str) or not _TICKER.fullmatch(params["symbol"])):
        raise DataAdapterError("unsupported")
    if endpoint == "historical-price-eod/light":
        try:
            start, end = (date.fromisoformat(params[name]) for name in ("from", "to"))
            if params["from"] != start.isoformat() or params["to"] != end.isoformat() or not 0 <= (end - start).days <= 365:
                raise ValueError()
        except (ValueError, TypeError):
            raise DataAdapterError("unsupported") from None
    elif endpoint != "profile":
        if params["period"] != "annual" or type(params["limit"]) is not int or not 1 <= params["limit"] <= 100:
            raise DataAdapterError("unsupported")


def _reject_constant(_):
    raise ValueError()


def _error_code(status, payload):
    if status == 401:
        return "authentication_failed"
    if status in {402, 403}:
        return "entitlement_denied"
    if status == 429:
        return "rate_limit"
    # Only classify bounded text; never return it or include it in an exception.
    message = " ".join(str(value)[:1024] for value in payload.values()).lower() if isinstance(payload, dict) else ""
    if "invalid api" in message or "invalid key" in message:
        return "authentication_failed"
    if "limit" in message and any(word in message for word in ("daily", "quota", "bandwidth")):
        return "quota"
    if any(word in message for word in ("premium", "subscription", "upgrade", "restricted endpoint")):
        return "entitlement_denied"
    return "network" if status >= 500 else "upstream_schema"


def _download(profile, endpoint, params, *, context):
    _validate_params(endpoint, params)
    context.begin_operation()
    connection = None
    finished = Event()
    watcher = None
    active_socket = None
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
                    wake_blocked_socket(sock)
                    return

        watcher = Thread(target=stop_socket, daemon=True)
        watcher.start()
        connection.connect()
        active_socket = connection.sock
        context.check_active()
        connection.sock.settimeout(context.remaining_seconds())
        # Official host and endpoint allowlist are fixed. No redirect or URL logging.
        connection.request("GET", "/stable/" + endpoint + "?" + urlencode(dict(params, apikey=profile.api_key)),
                           headers={"Accept": "application/json", "User-Agent": "ir-search/0.1", "Accept-Encoding": "identity"})
        response = connection.getresponse()
        if 300 <= response.status < 400:
            raise DataAdapterError("entitlement_denied")
        if response.status in {401, 402, 403, 429}:
            raise DataAdapterError(_error_code(response.status, None))
        length = response.getheader("Content-Length")
        if length is not None and (not length.isdigit() or int(length) > _MAX_BYTES):
            raise DataAdapterError("upstream_schema")
        content = bytearray()
        while True:
            context.check_active()
            if active_socket is not None:
                active_socket.settimeout(context.remaining_seconds())
            chunk = response.read1(min(65536, _MAX_BYTES + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > _MAX_BYTES:
                raise DataAdapterError("upstream_schema")
        context.check_active()
        try:
            payload = json.loads(content, parse_float=Decimal, parse_constant=_reject_constant)
        except (ValueError, UnicodeError, RecursionError):
            raise DataAdapterError("network" if response.status >= 500 else "upstream_schema") from None
        if response.status != 200 or not isinstance(payload, list):
            raise DataAdapterError(_error_code(response.status, payload))
        if len(payload) > 1000 or any(not isinstance(row, dict) for row in payload):
            raise DataAdapterError("upstream_schema")
        return _Response(payload, datetime.now(timezone.utc))
    except (RequestStopped, DataAdapterError):
        raise
    except ssl.SSLError:
        context.check_active()
        raise DataAdapterError("tls_error") from None
    except (TimeoutError, socket.timeout):
        context.check_active()
        raise DataAdapterError("timeout") from None
    except (OSError, http.client.HTTPException, ValueError):
        context.check_active()
        raise DataAdapterError("network") from None
    finally:
        finished.set()
        if connection is not None:
            connection.close()
        if watcher is not None:
            watcher.join(timeout=0.2)


class _Client:
    """One credential/config scope; serialized starts and a small memory-only cache."""
    def __init__(self, profile, *, transport=None, clock=None):
        self._profile = profile
        self._transport = transport or _download
        self._clock = clock or time.monotonic
        self._lock = Lock()
        self._cache = OrderedDict()
        self._last_start = float("-inf")

    def fetch(self, endpoint, params, *, context, budget):
        _validate_params(endpoint, params)
        key = (endpoint, tuple(sorted(params.items())))
        while not self._lock.acquire(timeout=min(0.05, context.remaining_seconds())):
            context.check_active()
        try:
            context.check_active()
            cached = self._cache.get(key)
            if cached and self._clock() - cached[0] < self._profile.cache_ttl_seconds:
                self._cache.move_to_end(key)
                return _Response(copy.deepcopy(cached[1].records), cached[1].fetched_at, True)
            while self._clock() - self._last_start < 1.0:
                context.check_active()
                context._cancelled.wait(min(0.05, context.remaining_seconds()))
            context.check_active()
            budget.consume()
            self._last_start = self._clock()
            result = self._transport(self._profile, endpoint, params, context=context)
            context.check_active()
            if self._profile.cache_ttl_seconds:
                self._cache[key] = (self._clock(), copy.deepcopy(result))
                self._cache.move_to_end(key)
                while len(self._cache) > 8:
                    self._cache.popitem(last=False)
            return result
        finally:
            self._lock.release()


@lru_cache(maxsize=8)
def _client_for(profile):
    return _Client(profile)
