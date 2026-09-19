import hashlib
import ssl
import sys
import types
from dataclasses import replace

import pytest

from ir_search.context import RequestContext, RequestStopped
from ir_search.infrastructure.credentials import MySQLProfile
from ir_search.infrastructure.mysql import _select, _tls_context, _connect
from ir_search.registry import DataAdapterError

PROFILE = MySQLProfile("wind_mysql", "host", "db", "user", "must_not_escape")
SQL = "SELECT S_INFO_WINDCODE FROM asharedescription ORDER BY S_INFO_WINDCODE LIMIT %s"


class Connection:
    def __init__(self, failure=None):
        self.events = []
        self.failure = failure

    def cursor(self):
        self.events.append("cursor")
        return self

    def execute(self, sql, params=None):
        self.events.append((sql, params))
        if sql.startswith("SELECT") and self.failure:
            raise self.failure

    def fetchmany(self, limit):
        self.events.append(("fetchmany", limit))
        return [{"S_INFO_WINDCODE": "000001.SZ"}]

    def rollback(self):
        self.events.append("rollback")

    def close(self):
        self.events.append("close")


def test_mysql_is_bounded_parameterized_read_only_and_closed():
    connection = Connection()
    context = RequestContext()
    rows = _select(PROFILE, SQL, (2,), max_rows=2, context=context, connector=lambda *a: connection)
    assert len(rows) == 1 and context.operations == 1
    statements = [event[0] for event in connection.events if isinstance(event, tuple)]
    assert statements[:3] == ["SET SESSION MAX_EXECUTION_TIME = %s", "START TRANSACTION READ ONLY", SQL]
    assert connection.events[-3:] == ["close", "rollback", "close"]


@pytest.mark.parametrize("number, code", [(1045,"entitlement_denied"), (1142,"entitlement_denied"), (1054,"upstream_schema"),
                                         (1146,"upstream_schema"), (1226,"quota"), (3024,"timeout"), (2026,"tls_error"), (2003,"network")])
def test_mysql_failures_close_connection_and_remove_upstream_secrets(number, code):
    connection = Connection(RuntimeError(number, "must_not_escape"))
    with pytest.raises(DataAdapterError) as caught:
        _select(PROFILE, SQL, (2,), max_rows=2, context=RequestContext(), connector=lambda *a: connection)
    assert caught.value.code == code and "must_not_escape" not in str(caught.value)
    assert connection.events[-3:] == ["close", "rollback", "close"]


@pytest.mark.parametrize("sql", ["DELETE FROM asharedescription LIMIT %s", "SELECT * FROM secret LIMIT %s",
                               "SELECT * FROM asharedescription JOIN SecuMain LIMIT %s", SQL + "; SELECT 1",
                               "SELECT * FROM asharedescription", "SELECT SLEEP(5) FROM asharedescription LIMIT %s"])
def test_arbitrary_or_unbounded_sql_is_rejected_before_connection(sql):
    with pytest.raises(DataAdapterError, match="unsupported"):
        _select(PROFILE, sql, (), max_rows=2, context=RequestContext(), connector=lambda *a: pytest.fail("network"))


def test_cancelled_request_never_connects():
    context = RequestContext()
    context.cancel()
    with pytest.raises(RequestStopped, match="cancelled"):
        _select(PROFILE, SQL, (2,), max_rows=2, context=context, connector=lambda *a: pytest.fail("network"))


def test_tls_default_requires_valid_certificate_and_hostname():
    context = _tls_context(PROFILE)
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2


def test_pinned_ca_mismatch_cannot_disable_tls(tmp_path):
    ca = tmp_path / "ca.pem"
    ca.write_text("invalid PEM")
    profile = replace(PROFILE, provider="jydb", tls_mode="pinned_ca", ssl_ca=str(ca), ca_sha256="a"*64)
    with pytest.raises(DataAdapterError, match="tls_error"):
        _tls_context(profile)


def test_server_without_tls_is_rejected_before_authentication(monkeypatch):
    authenticated = []

    class DriverConnection:
        def __init__(self, **kwargs):
            self.server_capabilities = 0
            self._request_authentication()

        def _request_authentication(self):
            authenticated.append(True)

    module = types.ModuleType("pymysql")
    module.connections = types.SimpleNamespace(Connection=DriverConnection)
    module.cursors = types.SimpleNamespace(DictCursor=object)
    constants = types.ModuleType("pymysql.constants")
    constants.CLIENT = types.SimpleNamespace(SSL=2048)
    monkeypatch.setitem(sys.modules, "pymysql", module)
    monkeypatch.setitem(sys.modules, "pymysql.constants", constants)
    with pytest.raises(DataAdapterError, match="tls_not_supported"):
        _connect(PROFILE, 2)
    assert not authenticated


def test_real_optional_driver_runs_tls_guard_before_authentication(monkeypatch):
    pymysql = pytest.importorskip("pymysql")

    def connect_without_network(self, *args, **kwargs):
        self.server_capabilities = 0
        self._request_authentication()

    monkeypatch.setattr(pymysql.connections.Connection, "connect", connect_without_network)
    with pytest.raises(DataAdapterError, match="tls_not_supported"):
        _connect(PROFILE, 2)


def test_expired_connect_does_not_start_queries():
    now = [0.0]
    context = RequestContext(timeout_seconds=1, _clock=lambda: now[0])
    connection = Connection()
    def connect(*args):
        now[0] = 2.0
        return connection
    with pytest.raises(RequestStopped, match="deadline_exceeded"):
        _select(PROFILE, SQL, (2,), max_rows=2, context=context, connector=connect)
    assert connection.events == ["rollback", "close"]


@pytest.mark.parametrize('cancel', [False, True])
def test_blocked_read_is_interrupted_lock_released_and_next_request_recovers(cancel):
    import socket
    import threading
    import time
    reader, writer = socket.socketpair()
    connection = Connection()
    connection._sock = reader
    def fetchmany(limit):
        reader.recv(1)  # Watcher shutdown must release this blocking read.
        raise OSError('must_not_escape')
    connection.fetchmany = fetchmany
    context = RequestContext(timeout_seconds=10 if cancel else 0.15)
    timer = threading.Timer(0.15, context.cancel) if cancel else None
    if timer: timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(RequestStopped, match='cancelled' if cancel else 'deadline_exceeded'):
            _select(PROFILE, SQL, (2,), max_rows=2, context=context, connector=lambda *a: connection)
        assert time.monotonic() - started < 2
    finally:
        if timer: timer.join()
        reader.close(); writer.close()
    assert _select(PROFILE, SQL, (2,), max_rows=2, context=RequestContext(), connector=lambda *a: Connection())


def test_cancelled_lock_wait_does_not_connect():
    import threading
    from ir_search.infrastructure.mysql import _LOCK
    context = RequestContext(timeout_seconds=10)
    timer = threading.Timer(0.1, context.cancel)
    with _LOCK:
        timer.start()
        try:
            with pytest.raises(RequestStopped, match='cancelled'):
                _select(PROFILE, SQL, (2,), max_rows=2, context=context, connector=lambda *a: pytest.fail('connect'))
        finally:
            timer.join()


@pytest.mark.parametrize('plugin', ['mysql_native_password', 'mysql_clear_password', 'caching_sha2_password'])
def test_non_tls_uses_only_explicit_native_auth_and_never_downgrades(monkeypatch, plugin):
    seen = {}
    class DriverConnection:
        def __init__(self, **kwargs):
            seen.update(kwargs)
            self._auth_plugin_name = plugin
            self._request_authentication()
        def _request_authentication(self):
            seen['authenticated'] = True
        def _process_auth(self, name, packet):
            seen['switched'] = name
    module = types.ModuleType('pymysql')
    module.connections = types.SimpleNamespace(Connection=DriverConnection)
    module.cursors = types.SimpleNamespace(DictCursor=object)
    constants = types.ModuleType('pymysql.constants')
    constants.CLIENT = types.SimpleNamespace(SSL=2048)
    monkeypatch.setitem(sys.modules, 'pymysql', module)
    monkeypatch.setitem(sys.modules, 'pymysql.constants', constants)
    profile = replace(PROFILE, tls_mode='disabled')
    if plugin != 'mysql_native_password':
        with pytest.raises(DataAdapterError, match='unsupported'):
            _connect(profile, 2)
        assert 'authenticated' not in seen
    else:
        connection = _connect(profile, 2)
        assert seen['ssl_disabled'] is True and 'ssl' not in seen
        with pytest.raises(DataAdapterError, match='unsupported'):
            connection._process_auth(b'mysql_clear_password', None)
        assert 'switched' not in seen
    with pytest.raises(DataAdapterError, match='source_config_error'):
        _connect(replace(profile, provider='jydb'), 2)
