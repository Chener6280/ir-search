import json
import socket
import ssl
import time
from datetime import datetime, timezone
from threading import Event, Thread

import pytest

from ir_search.context import RequestContext, RequestStopped
from ir_search.infrastructure.credentials import FMPProfile
from ir_search.infrastructure import fmp
from ir_search.registry import DataAdapterError

KEY = "offline_test_private_key"
PROFILE = FMPProfile(KEY)


class Response:
    status = 200
    def __init__(self, content=b'[{"symbol":"AAPL","price":1.25}]', status=200, length=None):
        self.content, self.status, self.length = content, status, length
    def getheader(self, name):
        return self.length
    def read1(self, size):
        result, self.content = self.content[:size], self.content[size:]
        return result


class Socket:
    def settimeout(self, value):
        assert 0 < value <= 30
    def shutdown(self, *_):
        pass


class Connection:
    def __init__(self, host, **kwargs):
        assert host == "financialmodelingprep.com"
        tls = kwargs["context"]
        assert tls.check_hostname and tls.verify_mode == ssl.CERT_REQUIRED
        assert tls.minimum_version >= ssl.TLSVersion.TLSv1_2
        self.sock = Socket()
        self.closed = False
        self.requests = []
        self.response = Response()
    def connect(self):
        pass
    def request(self, method, path, headers):
        assert method == "GET" and path.startswith("/stable/profile?symbol=AAPL&apikey=")
        self.requests.append(path)
    def getresponse(self):
        return self.response
    def close(self):
        self.closed = True


def connection_fixture(monkeypatch, response=None):
    tls = ssl.create_default_context()
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    connection = Connection("financialmodelingprep.com", context=tls)
    if response is not None:
        connection.response = response
    def factory(*args, **kwargs):
        Connection(*args, **kwargs)  # Validate the production TLS settings, too.
        return connection
    monkeypatch.setattr(fmp.http.client, "HTTPSConnection", factory)
    return connection


def test_https_decimal_json_and_closed_connection(monkeypatch):
    connection = connection_fixture(monkeypatch)
    context = RequestContext()
    result = fmp._download(PROFILE, "profile", {"symbol": "AAPL"}, context=context)
    assert str(result.records[0]["price"]) == "1.25"
    assert context.operations == 1 and connection.closed
    assert KEY not in repr(result)
    assert len(connection.requests) == 1


@pytest.mark.parametrize("status,body,code", [
    (401, b"secret", "authentication_failed"), (402, b"secret", "entitlement_denied"),
    (403, b"secret", "entitlement_denied"), (429, b"secret", "rate_limit"),
    (302, b"secret", "entitlement_denied"), (500, b"secret", "network"),
    (200, b'{"Error Message":"Invalid API KEY"}', "authentication_failed"),
    (200, b'{"Error Message":"Daily limit reached"}', "quota"),
    (200, b'{"Error Message":"Premium subscription required"}', "entitlement_denied"),
    (200, b"not json", "upstream_schema"), (200, b'[NaN]', "upstream_schema"),
    (200, b'[1]', "upstream_schema"), (200, b'{"unexpected":1}', "upstream_schema"),
])
def test_typed_errors_never_include_auth_urls_or_retry(monkeypatch, status, body, code):
    connection = connection_fixture(monkeypatch, Response(body, status))
    with pytest.raises(DataAdapterError) as caught:
        fmp._download(PROFILE, "profile", {"symbol": "AAPL"}, context=RequestContext())
    assert caught.value.code == code and KEY not in str(caught.value)
    assert connection.closed and len(connection.requests) == 1


@pytest.mark.parametrize("response", [Response(b"[]", length="1048577"), Response(b"[]", length="invalid"),
    Response(b"x" * 1048577), Response(json.dumps([{}] * 1001).encode())])
def test_response_size_limits(monkeypatch, response):
    connection = connection_fixture(monkeypatch, response)
    with pytest.raises(DataAdapterError, match="upstream_schema"):
        fmp._download(PROFILE, "profile", {"symbol": "AAPL"}, context=RequestContext())
    assert connection.closed


@pytest.mark.parametrize("endpoint,params", [("https://evil.invalid", {"symbol": "AAPL"}),
    ("profile", {"symbol": "AAPL&apikey=wrong"}), ("profile", {"symbol": "AAPL", "apikey": KEY}),
    ("historical-price-eod/light", {"symbol": "AAPL", "from": "2020-01-01", "to": "2026-01-01"}),
    ("income-statement", {"symbol": "AAPL", "period": "quarter", "limit": 5}),
    ("income-statement", {"symbol": "AAPL", "period": "annual", "limit": True})])
def test_endpoint_allowlist_before_network(monkeypatch, endpoint, params):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid request reached network")
    monkeypatch.setattr(fmp.http.client, "HTTPSConnection", forbidden)
    context = RequestContext()
    with pytest.raises(DataAdapterError, match="unsupported"):
        fmp._download(PROFILE, endpoint, params, context=context)
    assert context.operations == 0


@pytest.mark.parametrize("exception,code", [(ssl.SSLError("secret"), "tls_error"),
    (socket.timeout("secret"), "timeout"), (OSError("secret"), "network")])
def test_transport_exceptions_are_sanitized(monkeypatch, exception, code):
    connection = connection_fixture(monkeypatch)
    def fail():
        raise exception
    connection.connect = fail
    with pytest.raises(DataAdapterError, match=code) as caught:
        fmp._download(PROFILE, "profile", {"symbol": "AAPL"}, context=RequestContext())
    assert "secret" not in str(caught.value) and connection.closed


def test_cancel_blocked_socket_and_reject_after_deadline(monkeypatch):
    connection = connection_fixture(monkeypatch)
    ready, released = Event(), Event()
    connection.sock.shutdown = lambda *_: released.set()
    def blocked_read(_):
        ready.set()
        assert released.wait(2), "Cancellation did not close socket"
        return b""
    connection.response.read1 = blocked_read
    context = RequestContext()
    errors = []
    def run():
        try:
            fmp._download(PROFILE, "profile", {"symbol": "AAPL"}, context=context)
        except RequestStopped as exc:
            errors.append(exc.code)
    thread = Thread(target=run)
    thread.start()
    assert ready.wait(2)
    context.cancel()
    thread.join(2)
    assert not thread.is_alive() and errors == ["cancelled"] and connection.closed


def test_cache_preserves_fetch_time_and_isolates_mutations_expiry_and_credentials():
    clock = [10.0]
    calls = []
    fetched = datetime(2026, 9, 15, tzinfo=timezone.utc)
    def transport(profile, endpoint, params, *, context):
        context.begin_operation()
        calls.append(params)
        return fmp._Response([{"symbol": params["symbol"]}], fetched)
    client = fmp._Client(PROFILE, transport=transport, clock=lambda: clock[0])
    first = client.fetch("profile", {"symbol": "AAPL"}, context=RequestContext(), budget=fmp._Budget(1))
    first.records[0]["symbol"] = "MUTATED"
    second_context = RequestContext()
    cached = client.fetch("profile", {"symbol": "AAPL"}, context=second_context, budget=fmp._Budget(0))
    assert cached.cache_hit and cached.fetched_at == fetched and cached.records[0]["symbol"] == "AAPL"
    assert second_context.operations == 0 and len(calls) == 1
    clock[0] += 61
    assert not client.fetch("profile", {"symbol": "AAPL"}, context=RequestContext(), budget=fmp._Budget(1)).cache_hit
    assert len(calls) == 2
    for index in range(12):
        clock[0] += 2
        client.fetch("profile", {"symbol": "S" + str(index)}, context=RequestContext(), budget=fmp._Budget(1))
    assert len(client._cache) == 8
    assert fmp._client_for(PROFILE) is fmp._client_for(PROFILE)
    assert fmp._client_for(PROFILE) is not fmp._client_for(FMPProfile("different_test_key"))


def test_failed_requests_not_cached_budget_counted_and_rate_wait_cancellable():
    def transport(*args, **kwargs):
        raise DataAdapterError("rate_limit")
    clock = [10.0]
    client = fmp._Client(PROFILE, transport=transport, clock=lambda: clock[0])
    budget = fmp._Budget(1)
    with pytest.raises(DataAdapterError, match="rate_limit"):
        client.fetch("profile", {"symbol": "AAPL"}, context=RequestContext(), budget=budget)
    assert budget.used == 1 and not client._cache
    clock[0] += 2
    with pytest.raises(DataAdapterError, match="fmp_request_budget_exceeded"):
        client.fetch("profile", {"symbol": "AAPL"}, context=RequestContext(), budget=budget)
    client._last_start = clock[0]
    with pytest.raises(RequestStopped, match="deadline_exceeded"):
        client.fetch("profile", {"symbol": "AAPL"}, context=RequestContext(timeout_seconds=0.02), budget=fmp._Budget(1))
    assert not client._lock.locked()
