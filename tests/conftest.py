"""Keep offline tests isolated from the user's real credential file."""
import pytest


@pytest.fixture(autouse=True)
def isolated_source_credentials(tmp_path, monkeypatch, request):
    if request.node.get_closest_marker("live"):
        return
    monkeypatch.setenv("IR_SEARCH_LIVE", "0")
    monkeypatch.setenv("IR_SEARCH_RUN_LIVE_TESTS", "0")
    monkeypatch.delenv("IR_SEARCH_MCP_MODE", raising=False)
    monkeypatch.delenv("IR_SEARCH_ALLOW_PRIVATE_NETWORK", raising=False)
    monkeypatch.delenv("IR_SEARCH_OUTPUT_ROOT", raising=False)
    path = tmp_path / "test_sources.env"
    path.write_text("# Offline tests never load user credentials.\n", encoding="utf-8")
    path.chmod(0o600)
    monkeypatch.setenv("IR_SEARCH_CREDENTIALS_FILE", str(path))


@pytest.fixture(autouse=True)
def offline_network_guard(monkeypatch, request):
    """Offline tests may use loopback servers; external sockets/DNS must be mocked."""
    if request.node.get_closest_marker("live"):
        if __import__('os').environ.get("IR_SEARCH_RUN_LIVE_TESTS") != "1":
            pytest.skip("live tests require explicit IR_SEARCH_RUN_LIVE_TESTS=1")
        return
    import ipaddress
    import socket
    original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex
    original_resolve = socket.getaddrinfo
    def local(host):
        if host in ("localhost", b"localhost", None): return True
        try: return ipaddress.ip_address(host).is_loopback
        except (ValueError, TypeError): return False
    def guard(address):
        if isinstance(address, tuple) and not local(address[0]):
            raise AssertionError("offline_external_network_forbidden")
    def connect(sock, address):
        guard(address)
        return original_connect(sock, address)
    def connect_ex(sock, address):
        guard(address)
        return original_connect_ex(sock, address)
    def resolve(host, *args, **kwargs):
        if not local(host): raise AssertionError("offline_external_dns_forbidden")
        return original_resolve(host, *args, **kwargs)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
