"""Short-lived, serialized connections for fixed SELECT statements only."""
from __future__ import annotations

import hashlib
import math
import re
import ssl
import socket
import threading
from pathlib import Path

from .credentials import MySQLProfile
from ir_search.context import RequestStopped
from ir_search.registry import DataAdapterError

_LOCK = threading.Lock()  # Both providers can share constrained customer accounts.
_TABLES = {"asharedescription", "ashareeodprices", "SecuMain", "LC_Announcement", "QT_DailyQuote", "LC_STIBDailyQuote"}
_TABLES.update({'ashareincome', 'asharebalancesheet', 'asharecashflow', 'LC_IncomeStatementAll',
    'LC_BalanceSheetAll', 'LC_CashFlowStatementAll', 'LC_STIBIncomeState', 'LC_STIBBalanceSheet', 'LC_STIBCashFlowState',
    'cfuturesdescription', 'cfuturescalendar', 'chinaoptiondescription', 'cindexfutureseodprices',
    'cbondfutureseodprices', 'ccommodityfutureseodprices', 'chinaoptioneodprices',
    'Fut_ContractMain', 'Fut_TradingQuote', 'Fut_DailyQuote', 'Opt_OptionContract', 'Opt_DailyQuote'})


_TABLES.update({'chinamutualfunddescription','chinamutualfundnav','chinamutualfundshare',
                'chinamutualfundstockportfolio','chinaclosedfundeodprice','MF_NetValue'})

def _tls_context(profile):
    try:
        if profile.tls_mode == "pinned_ca":
            pem = Path(profile.ssl_ca).expanduser().read_text(encoding="ascii")
            actual = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()
            if actual != profile.ca_sha256.lower():
                raise ValueError()
        context = ssl.create_default_context(cafile=str(Path(profile.ssl_ca).expanduser()) if profile.ssl_ca else None)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        if profile.tls_mode == "pinned_ca":
            # Matches the shipped JYDB CA profile: no DNS SAN; pinned trust anchor.
            context.check_hostname = False
            if hasattr(ssl, "VERIFY_X509_STRICT"):
                context.verify_flags &= ~ssl.VERIFY_X509_STRICT
        context.verify_mode = ssl.CERT_REQUIRED
        return context
    except Exception:
        raise DataAdapterError("tls_error") from None


def _connect(profile, timeout):
    try:
        import pymysql
        from pymysql.constants import CLIENT
    except ImportError:
        raise DataAdapterError("dependency_missing") from None

    if profile.tls_mode not in {"verify_identity", "pinned_ca", "disabled"}:
        raise DataAdapterError("source_config_error")
    unencrypted = profile.tls_mode == "disabled"
    if unencrypted and (profile.provider != "wind_mysql" or profile.ssl_ca or profile.ca_sha256):
        raise DataAdapterError("source_config_error")

    class ConfiguredConnection(pymysql.connections.Connection):
        def _request_authentication(self):
            if unencrypted:
                if self._auth_plugin_name != "mysql_native_password":
                    raise DataAdapterError("unsupported")
            elif not self.server_capabilities & CLIENT.SSL:
                raise DataAdapterError("tls_not_supported")
            return super()._request_authentication()

        def _process_auth(self, plugin_name, auth_packet):
            if unencrypted and plugin_name != b"mysql_native_password":
                raise DataAdapterError("unsupported")
            return super()._process_auth(plugin_name, auth_packet)

    transport = {"ssl_disabled": True} if unencrypted else {"ssl": _tls_context(profile)}
    return ConfiguredConnection(host=profile.host, port=profile.port, database=profile.database,
                         user=profile.user, password=profile.password, **transport,
                         connect_timeout=max(1, min(10, math.ceil(timeout))),
                         read_timeout=timeout, write_timeout=timeout, charset="utf8mb4",
                         autocommit=False, local_infile=False, cursorclass=pymysql.cursors.DictCursor)


def _select(profile: MySQLProfile, sql: str, params: tuple, *, max_rows: int, context, connector=None):
    """Internal transport; SDK/MCP never accept raw SQL or database identifiers."""
    tables = re.findall(r"\bFROM\s+`?([a-zA-Z0-9_]+)`?", sql, re.I)
    if (not sql.startswith("SELECT ") or len(tables) != 1 or tables[0] not in _TABLES
            or re.search(r";|--|/\*|\b(JOIN|UNION|INTO|OUTFILE|SLEEP|BENCHMARK|FOR\s+UPDATE)\b", sql, re.I)
            or not re.search(r"\bLIMIT %s$", sql) or not 1 <= max_rows <= 5001):
        raise DataAdapterError("unsupported")
    context.check_active()
    while not _LOCK.acquire(timeout=min(0.05, context.remaining_seconds())):
        context.check_active()
    connection = cursor = None
    finished = threading.Event()
    watcher = None
    try:
        context.begin_operation()
        connection = (connector or _connect)(profile, context.remaining_seconds())
        context.check_active()
        def interrupt_socket():
            # Shutdown wakes a blocked driver read. Never close another request's socket.
            while not finished.wait(0.05):
                try:
                    context.check_active()
                except RequestStopped:
                    sock = getattr(connection, "_sock", None)
                    if sock is not None:
                        try:
                            sock.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                    return
        watcher = threading.Thread(target=interrupt_socket, name="ir-search-sql-budget", daemon=True)
        watcher.start()
        cursor = connection.cursor()
        cursor.execute("SET SESSION MAX_EXECUTION_TIME = %s", (max(1, int(context.remaining_seconds() * 1000)),))
        cursor.execute("START TRANSACTION READ ONLY")
        context.check_active()
        # Connection setup has already spent part of the end-to-end budget.
        if hasattr(connection, "_read_timeout"):
            connection._read_timeout = context.remaining_seconds()
            connection._write_timeout = context.remaining_seconds()
        cursor.execute(sql, params)
        rows = cursor.fetchmany(max_rows)
        context.check_active()
        if not isinstance(rows, (tuple, list)) or any(not isinstance(row, dict) for row in rows):
            raise DataAdapterError("upstream_schema")
        return list(rows)
    except (DataAdapterError, RequestStopped):
        raise
    except Exception as exc:
        context.check_active()
        code = exc.args[0] if exc.args and type(exc.args[0]) is int else None
        kind = {1045: "entitlement_denied", 1044: "entitlement_denied", 1142: "entitlement_denied",
                1146: "upstream_schema", 1054: "upstream_schema", 1226: "quota", 1040: "rate_limit",
                1203: "rate_limit", 3024: "timeout", 2026: "tls_error"}.get(code, "network")
        if isinstance(exc, ssl.SSLError):
            kind = "tls_error"
        raise DataAdapterError(kind) from None
    finally:
        for obj, method in ((cursor, "close"), (connection, "rollback"), (connection, "close")):
            if obj is not None:
                try:
                    getattr(obj, method)()
                except Exception:
                    pass
        finished.set()
        if watcher is not None:
            watcher.join(timeout=0.2)
        _LOCK.release()
