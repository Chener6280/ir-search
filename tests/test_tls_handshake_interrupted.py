"""A TLS handshake that ends before the server answers is its own failure, the same on every system.

Offline: every server here listens on 127.0.0.1 only and the resolver is pointed at it.
"""
import errno
import socket
import ssl
import struct
import threading
import time
from types import SimpleNamespace

import pytest

from ir_search import RequestContext
from ir_search.context import RequestStopped
from ir_search.infrastructure import public_web as web
from ir_search.infrastructure.recovery import _recovery, _stop_source
from ir_search.models import FailureKind
from ir_search.registry import DataAdapterError

# A fatal TLS alert record (handshake_failure): the server did answer, at the TLS level.
ALERT = bytes([0x15, 0x03, 0x03, 0x00, 0x02, 0x02, 0x28])


@pytest.fixture
def server(monkeypatch):
    """Start one loopback server of the given kind and point the resolver at it."""
    listeners = []

    def start(kind):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listeners.append(listener)

        def run():
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            try:
                if kind == "reset":
                    conn.recv(4096)
                    conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("hh", 1, 0))
                elif kind == "silent_then_close":
                    time.sleep(0.4)
                elif kind == "alert":
                    conn.recv(4096)
                    conn.sendall(ALERT)
                    time.sleep(0.2)
                elif kind == "hold":
                    time.sleep(3)
            finally:
                conn.close()

        threading.Thread(target=run, daemon=True).start()
        address = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", listener.getsockname()[1]))
        monkeypatch.setattr(web, "_resolve", lambda *args: address)

    yield start
    for listener in listeners:
        listener.close()


def code_of(call):
    with pytest.raises(DataAdapterError) as caught:
        call()
    return caught.value.code


def fetch(url="https://example.org/a", **options):
    return web._request(url, context=options.pop("context", None) or RequestContext(timeout_seconds=5), **options)


# --- the three shapes measured on this project's Windows computer, one code everywhere ---

@pytest.mark.parametrize("kind", ["fin", "reset", "silent_then_close"])
def test_a_handshake_cut_off_after_tcp_connect_has_one_code_on_every_system(server, kind):
    server(kind)
    assert code_of(fetch) == "tls_handshake_interrupted"


def test_a_tls_alert_is_an_answer_and_stays_tls_error(server):
    server("alert")
    assert code_of(fetch) == "tls_error"


def test_the_same_reset_without_tls_is_still_a_plain_network_failure(server):
    server("fin")
    assert code_of(lambda: fetch("http://example.org/a")) == "network"


# --- decided by where it happened: every exception the handshake call can end with ---

def handshake_raises(monkeypatch, server, error):
    server("hold")
    def wrap_socket(sock, server_hostname):
        raise error
    monkeypatch.setattr(web.ssl, "create_default_context",
                        lambda: SimpleNamespace(minimum_version=None, wrap_socket=wrap_socket))


def ssl_error(reason):
    error = ssl.SSLError(1, f"[SSL: {reason}] synthetic")
    error.reason = reason
    return error


@pytest.mark.parametrize("error", [ConnectionResetError(), ConnectionAbortedError(), BrokenPipeError(),
                                   ssl.SSLEOFError(8, "EOF occurred in violation of protocol"),
                                   ssl.SSLZeroReturnError(6, "TLS/SSL connection has been closed"),
                                   ssl_error("UNEXPECTED_EOF_WHILE_READING")], ids=lambda e: type(e).__name__)
def test_every_way_a_connection_can_end_inside_the_handshake_is_the_new_code(monkeypatch, server, error):
    handshake_raises(monkeypatch, server, error)
    assert code_of(fetch) == "tls_handshake_interrupted"


@pytest.mark.parametrize("error,code", [
    (ssl.SSLCertVerificationError(1, "certificate verify failed: unable to get local issuer certificate"), "tls_error"),
    (ssl_error("SSLV3_ALERT_HANDSHAKE_FAILURE"), "tls_error"),
    (ssl_error("WRONG_VERSION_NUMBER"), "tls_error"),
    (ssl.SSLError(1, "unspecified"), "tls_error"),
    (socket.timeout("The handshake operation timed out"), "timeout"),
    (OSError(errno.ENETUNREACH, "Network is unreachable"), "network"),
    (ValueError("unexpected"), "network"),
], ids=lambda value: value if isinstance(value, str) else type(value).__name__)
def test_every_other_handshake_failure_keeps_the_code_it_had(monkeypatch, server, error, code):
    handshake_raises(monkeypatch, server, error)
    assert code_of(fetch) == code


# --- a stop is checked before anything is mapped ---

def test_a_cancellation_that_closes_the_socket_mid_handshake_stays_a_cancellation(server):
    server("hold")
    context = RequestContext(timeout_seconds=5)
    threading.Timer(0.2, context.cancel).start()
    with pytest.raises(RequestStopped) as stopped:
        fetch(context=context)
    assert stopped.value.code == "cancelled"


def test_a_stop_is_checked_first_even_when_the_handshake_reports_a_reset(monkeypatch, server):
    context = RequestContext(timeout_seconds=5)
    server("hold")
    def wrap_socket(sock, server_hostname):
        context.cancel()  # the watcher closed the socket: the handshake call sees a reset
        raise ConnectionResetError()
    monkeypatch.setattr(web.ssl, "create_default_context",
                        lambda: SimpleNamespace(minimum_version=None, wrap_socket=wrap_socket))
    with pytest.raises(RequestStopped) as stopped:
        fetch(context=context)
    assert stopped.value.code == "cancelled"


def test_a_handshake_that_outlasts_the_deadline_is_never_the_new_code(server):
    server("hold")
    with pytest.raises((RequestStopped, DataAdapterError)) as ended:
        fetch(context=RequestContext(timeout_seconds=0.4))
    assert ended.value.code in {"deadline_exceeded", "timeout"}


# --- what the code means ---

def test_the_code_is_a_network_failure_with_guidance_of_its_own():
    assert DataAdapterError("tls_handshake_interrupted").failure_kind is FailureKind.NETWORK
    assert DataAdapterError("tls_error").failure_kind is FailureKind.BLOCKED_BY_POLICY  # unchanged
    assert DataAdapterError("network").failure_kind is FailureKind.NETWORK               # unchanged
    guidance = _recovery("tls_handshake_interrupted")
    assert guidance["category"] == "availability" and _stop_source("tls_handshake_interrupted")
    assert guidance["next_action"] not in {_recovery("network")["next_action"], _recovery("tls_error")["next_action"]}
    for expected in ("TLS 握手", "不是证书问题", "重试无益", "如实记录"):
        assert expected in guidance["next_action"]
    assert "代理" not in guidance["next_action"] and "proxy" not in guidance["next_action"].lower()
