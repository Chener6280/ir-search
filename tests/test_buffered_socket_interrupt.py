"""Real loopback HTTP, TLS and PyMySQL file readers; no provider or credentials."""
from datetime import datetime, timedelta, timezone
import http.client
import socket
import ssl
import threading
import time

import pytest

from ir_search.infrastructure._interrupt import wake_blocked_socket


@pytest.fixture
def tls_contexts(tmp_path):
    pytest.importorskip('cryptography')
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    import ipaddress
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), False)
        .sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / 'synthetic.pem', tmp_path / 'ephemeral-test-key.pem'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
    key_path.chmod(0o600)
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(cert_path, key_path)
    client = ssl.create_default_context(cafile=str(cert_path))
    return server, client


@pytest.mark.parametrize('kind', ['http', 'tls', 'mysql'])
def test_request_owned_buffered_read_cancellation_and_later_socket_survives(kind, request):
    # Repetition increases the chance of OS handle reuse after the cancellation close.
    contexts = request.getfixturevalue('tls_contexts') if kind == 'tls' else None
    if kind == 'mysql':
        pymysql = pytest.importorskip('pymysql')
    for _ in range(5):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0)); listener.listen(1)
        release, ready, reading = threading.Event(), threading.Event(), threading.Event()
        errors, outcome = [], []
        def serve():
            try:
                with listener.accept()[0] as raw:
                    conn = contexts[0].wrap_socket(raw, server_side=True) if contexts else raw
                    try:
                        if kind != 'mysql':
                            conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n')
                        ready.set()
                        release.wait(5)
                    finally:
                        conn.close()
            except Exception as exc:
                errors.append(type(exc).__name__); ready.set()
        server_thread = threading.Thread(target=serve, daemon=True); server_thread.start()
        reader = socket.create_connection(listener.getsockname(), timeout=5)
        if contexts: reader = contexts[1].wrap_socket(reader, server_hostname='127.0.0.1')
        reader.settimeout(5)
        assert ready.wait(3) and not errors
        if kind == 'mysql':
            owner = pymysql.connections.Connection(defer_connect=True)
            owner._sock, owner._rfile = reader, reader.makefile('rb')
            owner._read_timeout = 5
            read, close = lambda: owner._read_bytes(1), owner._force_close
        else:
            owner = http.client.HTTPResponse(reader)
            owner.begin()
            read, close = lambda: owner.read(1), owner.close
        def consume():
            reading.set()
            try: outcome.append(read())
            except Exception as exc: outcome.append(type(exc).__name__)
        worker = threading.Thread(target=consume, daemon=True); worker.start()
        assert reading.wait(1)
        time.sleep(.02)  # Enter a real buffered read, rather than cancelling before dispatch.
        started = time.monotonic()
        try:
            wake_blocked_socket(reader)
            worker.join(2)
            assert not worker.is_alive() and time.monotonic() - started < 2
            assert outcome
            # Open another handle before closing wrappers from the cancelled request.
            newer, peer = socket.socketpair()
            try:
                close(); reader.close(); wake_blocked_socket(reader)
                newer.sendall(b'x'); peer.settimeout(1)
                assert peer.recv(1) == b'x'
            finally:
                newer.close(); peer.close()
        finally:
            release.set(); close(); reader.close(); listener.close()
            worker.join(5); server_thread.join(5)
        assert not worker.is_alive() and not server_thread.is_alive() and not errors
