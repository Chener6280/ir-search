"""Short-lived, authenticated CONNECT gate for the Xueqiu browser worker.

Chromium owns TLS and verifies the origin certificate. This gate resolves and
pins only fixed public origin addresses; it does not decrypt HTTPS or log URLs.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import secrets
import select
import socket
from threading import Lock

from ir_search.context import RequestStopped
from ir_search.registry import DataAdapterError
from .public_web import _resolve

HOSTS = frozenset({'xueqiu.com', 'assets.imedao.com', 'xqdoc.imedao.com',
                   'g.alicdn.com', 'o.alicdn.com'})


@dataclass
class _Gate:
    context: object
    password: str = field(default_factory=lambda: secrets.token_hex(24), repr=False)
    byte_limit: int = 24 * 1024 * 1024
    wire_bytes: int = 0
    connections: int = 0
    failure: str = ''
    lock: object = field(default_factory=Lock, repr=False)
    sockets: set = field(default_factory=set, repr=False)

    def authenticate(self, header):
        expected = 'Basic ' + base64.b64encode(('ir-search:' + self.password).encode()).decode()
        return isinstance(header, str) and header.isascii() and hmac.compare_digest(header, expected)

    def connect(self, authority):
        if authority not in {host + ':443' for host in HOSTS}:
            raise DataAdapterError('blocked_url')
        self.context.check_active()
        with self.lock:
            if self.connections >= 40: raise DataAdapterError('browser_budget_exhausted')
            self.connections += 1
        address = _resolve(authority[:-4], 443, self.context)
        family, kind, protocol, _, sockaddr = address
        sock = socket.socket(family, kind, protocol)
        try:
            sock.settimeout(min(10, self.context.remaining_seconds()))
            sock.connect(sockaddr)
            with self.lock: self.sockets.add(sock)
            return sock
        except BaseException:
            sock.close()
            raise

    def charge(self, size):
        self.context.check_active()
        with self.lock:
            if self.wire_bytes + size > self.byte_limit:
                self.failure = 'browser_budget_exhausted'
                raise DataAdapterError(self.failure)
            self.wire_bytes += size

    def close(self):
        with self.lock:
            for sock in list(self.sockets):
                try: sock.shutdown(socket.SHUT_RDWR)
                except OSError: pass
                sock.close()
            self.sockets.clear()


class _Handler(BaseHTTPRequestHandler):
    def do_CONNECT(self):
        gate = self.server.gate
        if not gate.authenticate(self.headers.get('Proxy-Authorization')):
            self.send_response(407)
            self.send_header('Proxy-Authenticate', 'Basic realm="ir-search"')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        upstream = None
        try:
            upstream = gate.connect(self.path)
            self.send_response(200, 'Connection Established')
            self.end_headers()
            self.connection.settimeout(1)
            upstream.settimeout(1)
            while True:
                gate.context.check_active()
                ready, _, _ = select.select([self.connection, upstream], [], [], .1)
                for origin in ready:
                    chunk = origin.recv(65536)
                    if not chunk: return
                    gate.charge(len(chunk))
                    (upstream if origin is self.connection else self.connection).sendall(chunk)
        except (DataAdapterError, RequestStopped) as exc:
            if exc.code != 'blocked_url' or self.path in {host+':443' for host in HOSTS}:
                gate.failure = gate.failure or exc.code
        except OSError:
            pass
        finally:
            if upstream is not None:
                with gate.lock: gate.sockets.discard(upstream)
                upstream.close()
            self.close_connection = True

    def do_GET(self): self.send_error(403)
    do_POST = do_PUT = do_DELETE = do_OPTIONS = do_HEAD = do_PATCH = do_GET
    def log_message(self, *args): pass


def _server(gate):
    server = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
    server.daemon_threads = True
    server.gate = gate
    return server
