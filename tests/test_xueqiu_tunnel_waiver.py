"""The Xueqiu CONNECT gate and its narrow fake-ip waiver: fixed hosts, port 443, bundled Chromium.

No network and no browser: DNS answers, sockets and the Playwright module are synthetic. Whether a
real Chromium verifies certificates cannot be shown here; what is pinned is everything this code
contributes to that premise.
"""
import ast
import asyncio
import io
import json
import os
from pathlib import Path
import socket
import sys
from types import SimpleNamespace

import pytest

from ir_search import RequestContext
from ir_search.infrastructure import _xueqiu_browser_worker as worker
from ir_search.infrastructure import _xueqiu_tunnel as tunnel
from ir_search.infrastructure import public_web as web
from ir_search.registry import DataAdapterError

KEY = "PUBLIC_WEB_FAKE_IP_HTTPS"
PLACEHOLDER, PUBLIC = "198.18.1.4", "93.184.216.34"
FIVE = frozenset({"xueqiu.com", "assets.imedao.com", "xqdoc.imedao.com", "g.alicdn.com", "o.alicdn.com"})
NEAR_MISSES = ["example.org", "sub.xueqiu.com", "www.xueqiu.com", "xueqiu.com.evil.example", "evilxueqiu.com",
               "XUEQIU.COM", "Xueqiu.com", "xueqiu.com.", " xueqiu.com", "xueqiu.com ", "alicdn.com", "imedao.com",
               "g.alicdn.com.cn", "G.ALICDN.COM", ""]


def row(ip, port=443):
    if ":" in ip:
        return (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, port, 0, 0))
    return (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))


def switch(value):
    path = Path(os.environ["IR_SEARCH_CREDENTIALS_FILE"])  # the per-test file made by conftest
    path.write_text(f"{KEY}={value}\n", encoding="utf-8")
    path.chmod(0o600)


def dns(monkeypatch, *ips):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [row(ip) for ip in ips])


def no_dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: pytest.fail("a refused name is never looked up"))


def code_of(call):
    with pytest.raises(DataAdapterError) as caught:
        call()
    return caught.value.code


def door(host, port=443, bundled=True):
    return web._resolve_browser_tunnel(host, port, RequestContext(), bundled)


# --- one list, shared, matched exactly ---

def test_the_five_origins_are_defined_once_and_shared():
    assert web._BROWSER_TUNNEL_HOSTS == FIVE and isinstance(web._BROWSER_TUNNEL_HOSTS, frozenset)
    assert tunnel.HOSTS is web._BROWSER_TUNNEL_HOSTS and worker.HOSTS is web._BROWSER_TUNNEL_HOSTS


@pytest.mark.parametrize("host", sorted(FIVE))
def test_each_fixed_origin_passes_a_placeholder_only_with_switch_and_bundled_chromium(monkeypatch, host):
    dns(monkeypatch, PLACEHOLDER)
    assert code_of(lambda: door(host)) == "fake_ip_blocked"  # switch off
    switch("true")
    assert door(host) == row(PLACEHOLDER)
    assert code_of(lambda: door(host, bundled=False)) == "fake_ip_blocked"


@pytest.mark.parametrize("host", NEAR_MISSES)
def test_any_other_name_is_refused_outright_even_with_everything_on(monkeypatch, host):
    switch("true")
    no_dns(monkeypatch)
    assert code_of(lambda: door(host)) == "blocked_url"


@pytest.mark.parametrize("port", [80, 8443, 444, 0, 4430, "443", None])
def test_any_port_but_443_is_refused_outright(monkeypatch, port):
    switch("true")
    no_dns(monkeypatch)
    assert code_of(lambda: door("xueqiu.com", port)) == "blocked_url"


@pytest.mark.parametrize("claim", [False, None, 0, 1, "true", "chromium", "yes", [True]])
def test_only_the_literal_true_counts_as_bundled_chromium(monkeypatch, claim):
    switch("true")
    dns(monkeypatch, PLACEHOLDER)
    assert code_of(lambda: door("xueqiu.com", bundled=claim)) == "fake_ip_blocked"


def test_bundled_chromium_defaults_to_no_waiver(monkeypatch):
    switch("true")
    dns(monkeypatch, PLACEHOLDER)
    assert code_of(lambda: web._resolve_browser_tunnel("xueqiu.com", 443, RequestContext())) == "fake_ip_blocked"


# --- the shared address rules still apply behind this door ---

@pytest.mark.parametrize("private", ["10.0.0.5", "127.0.0.1", "169.254.169.254", "192.168.1.1", "::1", "fc00::1"])
@pytest.mark.parametrize("placeholder_first", [True, False])
def test_a_placeholder_next_to_a_private_answer_is_refused_as_a_whole(monkeypatch, private, placeholder_first):
    switch("true")
    dns(monkeypatch, *((PLACEHOLDER, private) if placeholder_first else (private, PLACEHOLDER)))
    assert code_of(lambda: door("xueqiu.com")) == "blocked_url"
    dns(monkeypatch, private)
    assert code_of(lambda: door("xueqiu.com")) == "blocked_url"


def test_public_answers_pass_as_before_and_never_consult_the_switch(monkeypatch):
    monkeypatch.setattr(web, "_fake_ip_https_enabled", lambda: pytest.fail("no placeholder, nothing to decide"))
    dns(monkeypatch, PUBLIC)
    assert door("xueqiu.com") == row(PUBLIC) and door("xueqiu.com", bundled=False) == row(PUBLIC)


@pytest.mark.parametrize("value", ["TRUE", "1", "yes", ""])
def test_an_invalid_switch_value_is_a_configuration_error_here_too(monkeypatch, value):
    switch(value)
    dns(monkeypatch, PLACEHOLDER)
    assert code_of(lambda: door("xueqiu.com")) == "source_config_error"


def test_the_waiver_does_not_leak_into_the_general_door(monkeypatch):
    switch("true")
    dns(monkeypatch, PLACEHOLDER)
    for scheme in (None, "http", "ws", "tunnel", "connect"):
        assert code_of(lambda: web._resolve("xueqiu.com", 443, RequestContext(), scheme)) == "fake_ip_blocked"


# --- the gate ---

@pytest.fixture
def sockets(monkeypatch):
    connected = []

    class Socket:
        def settimeout(self, value): assert value > 0
        def connect(self, address): connected.append(address)
        def shutdown(self, *args): pass
        def close(self): pass

    monkeypatch.setattr(socket, "socket", lambda *args: Socket())
    return connected


def test_gate_connects_to_the_placeholder_only_for_bundled_chromium(monkeypatch, sockets):
    switch("true")
    dns(monkeypatch, PLACEHOLDER)
    assert tunnel._Gate(RequestContext()).bundled_chromium is False
    assert code_of(lambda: tunnel._Gate(RequestContext()).connect("xueqiu.com:443")) == "fake_ip_blocked"
    assert code_of(lambda: tunnel._Gate(RequestContext(), bundled_chromium=False).connect("xueqiu.com:443")) == "fake_ip_blocked"
    assert sockets == []
    gate = tunnel._Gate(RequestContext(), bundled_chromium=True)
    gate.connect("o.alicdn.com:443")
    gate.close()
    assert sockets == [(PLACEHOLDER, 443)]


def test_gate_follows_the_switch(monkeypatch, sockets):
    dns(monkeypatch, PLACEHOLDER)
    gate = tunnel._Gate(RequestContext(), bundled_chromium=True)
    assert code_of(lambda: gate.connect("xueqiu.com:443")) == "fake_ip_blocked" and sockets == []


@pytest.mark.parametrize("authority", ["xueqiu.com:8443", "xueqiu.com:80", "xueqiu.com", "xueqiu.com:0443", "xueqiu.com:443 ",
                                       "XUEQIU.COM:443", "sub.xueqiu.com:443", "xueqiu.com.evil.example:443",
                                       "evilxueqiu.com:443", "198.18.1.4:443", "xueqiu.com:443@evil.example"])
def test_gate_refuses_every_other_authority_before_any_lookup(monkeypatch, sockets, authority):
    switch("true")
    no_dns(monkeypatch)
    gate = tunnel._Gate(RequestContext(), bundled_chromium=True)
    assert code_of(lambda: gate.connect(authority)) == "blocked_url" and sockets == []


def test_gate_refuses_a_mixed_answer_as_a_whole(monkeypatch, sockets):
    switch("true")
    dns(monkeypatch, PLACEHOLDER, "10.0.0.5")
    gate = tunnel._Gate(RequestContext(), bundled_chromium=True)
    assert code_of(lambda: gate.connect("xueqiu.com:443")) == "blocked_url" and sockets == []


# --- channel guard: the gate is told about the browser that really starts ---

@pytest.mark.parametrize("payload,bundled", [({}, True), ({"channel": "chromium"}, True), ({"channel": "chrome"}, False),
                                              ({"channel": "msedge"}, False), ({"channel": "chrome-beta"}, False),
                                              ({"channel": "Chromium"}, False), ({"channel": "chromium "}, False),
                                              ({"channel": ""}, False), ({"channel": None}, False)])
def test_only_playwrights_own_chromium_counts_as_bundled(payload, bundled):
    assert worker._bundled_chromium(payload) is bundled
    launched = worker._launch_options(payload, "http://127.0.0.1:1", "secret")["channel"]
    assert launched == payload.get("channel", "chromium") and (launched == "chromium") is bundled


@pytest.mark.parametrize('driver', ['playwright', 'patchright'])
@pytest.mark.parametrize('channel', ['chromium', 'chrome'])
def test_alternative_driver_keeps_the_bundled_chromium_waiver_closed(driver, channel):
    payload = {'driver': driver, 'channel': channel}
    assert worker._bundled_chromium(payload) is (driver == 'playwright' and channel == 'chromium')
    assert worker._context_options()['ignore_https_errors'] is False


@pytest.mark.parametrize("channel,bundled", [("chromium", True), ("chrome", False), ("msedge", False)])
def test_worker_main_builds_the_gate_from_the_channel_it_launches(monkeypatch, tmp_path, channel, bundled):
    seen = {}
    async def read(payload, context, gate, proxy):
        seen.update(gate=gate, proxy=proxy, launch=worker._launch_options(payload, proxy, gate.password))
        return {"text": "ok"}
    monkeypatch.setattr(worker, "_read", read)
    output = tmp_path / "result.json"
    payload = {"url": "https://xueqiu.com/1/2", "timeout": 5, "operations": 3, "output": str(output), "channel": channel}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    worker.main()
    assert seen["gate"].bundled_chromium is bundled and seen["launch"]["channel"] == channel
    assert seen["proxy"].startswith("http://127.0.0.1:") and json.loads(output.read_text(encoding="utf-8"))["text"] == "ok"


# --- premises the waiver rests on, pinned ---

BYPASSES = ("ignore-certificate", "ignore-urlfetcher-cert", "allow-insecure", "allow-running-insecure", "disable-web-security",
            "ignore-ssl", "ssl-version", "no-sandbox", "proxy-bypass", "host-resolver", "host-rules", "test-type")


@pytest.mark.parametrize("payload", [{}, {"channel": "chrome", "headless": False}])
def test_launch_options_keep_every_certificate_check_on(payload):
    options = worker._launch_options(payload, "http://127.0.0.1:12345", "secret")
    assert set(options) == {"channel", "headless", "chromium_sandbox", "proxy", "args"}
    assert options["chromium_sandbox"] is True
    assert options["proxy"] == {"server": "http://127.0.0.1:12345", "bypass": "<-loopback>", "username": "ir-search",
                                "password": "secret"}
    assert not [arg for arg in options["args"] if any(bad in arg.lower() for bad in BYPASSES)]
    assert "--disable-quic" in options["args"]  # everything goes through the CONNECT gate, nothing over UDP
    context = worker._context_options()
    assert context["ignore_https_errors"] is False and context["accept_downloads"] is False
    assert context["service_workers"] == "block" and set(context) == {"ignore_https_errors", "accept_downloads",
                                                                      "service_workers", "viewport"}


def test_the_browser_is_really_started_with_those_options(monkeypatch):
    """A synthetic Playwright records what _read hands to it, then stops the read."""
    seen = {}

    class Browser:
        async def new_context(self, **options):
            seen["context"] = options
            raise DataAdapterError("browser_failed")
        async def close(self): seen["closed"] = True

    class Chromium:
        async def launch(self, **options):
            seen["launch"] = options
            return Browser()

    class Playwright:
        chromium = Chromium()
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False

    monkeypatch.setitem(sys.modules, "playwright.async_api", SimpleNamespace(async_playwright=lambda: Playwright()))
    gate = tunnel._Gate(RequestContext(), bundled_chromium=True)
    payload = {"url": "https://xueqiu.com/1/2", "channel": "chromium", "headless": True}
    with pytest.raises(DataAdapterError, match="browser_failed"):
        asyncio.run(worker._read(payload, RequestContext(), gate, "http://127.0.0.1:12345"))
    assert seen["launch"] == worker._launch_options(payload, "http://127.0.0.1:12345", gate.password)
    assert seen["context"] == worker._context_options() and seen["closed"]


def test_every_browser_request_is_limited_to_https_get_on_the_fixed_origins():
    target = "https://xueqiu.com/1/2"
    assert worker._allow(target, target=target, method="GET", kind="document", main=True)
    assert worker._allow("https://assets.imedao.com/a.js", target=target, method="GET", kind="script", main=False)
    for url, method in (("http://xueqiu.com/1/2", "GET"), ("https://sub.xueqiu.com/a.js", "GET"),
                        ("https://example.org/a.js", "GET"), ("https://assets.imedao.com/a.js", "POST")):
        assert not worker._allow(url, target=target, method=method, kind="script", main=False)


ALLOWED_IMPORTS = {"__future__", "base64", "dataclasses", "hmac", "http.server", "secrets", "select", "socket", "threading",
                   "ir_search.context", "ir_search.registry", ".public_web"}
TLS_NAMES = {"ssl", "wrap_socket", "wrap_bio", "SSLContext", "SSLSocket", "create_default_context", "do_handshake",
             "load_cert_chain", "load_verify_locations", "OpenSSL", "cryptography", "certifi", "starttls", "unwrap"}


def test_the_gate_module_stays_a_byte_relay_without_tls_code():
    """The waiver rests on the browser's end-to-end check, so the gate may never terminate TLS.

    Any new import, or any name that belongs to a TLS API, fails here and needs a fresh review.
    """
    tree = ast.parse(Path(tunnel.__file__).read_text(encoding="utf-8"))
    imports, from_transport = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            imports.add(module)
            if module == ".public_web":
                from_transport.update(alias.name for alias in node.names)
    assert imports == ALLOWED_IMPORTS
    assert from_transport == {"_BROWSER_TUNNEL_HOSTS", "_resolve_browser_tunnel"}  # its one DNS door, not _resolve
    used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not used & TLS_NAMES
    assert not any(isinstance(node, ast.Call) and getattr(node.func, "id", "") in {"__import__", "eval", "exec"}
                   for node in ast.walk(tree))
