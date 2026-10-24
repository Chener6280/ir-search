"""A proxy in fake-ip mode answers every DNS question with a 198.18.0.0/15 placeholder.

No real network: DNS answers, the credentials file and the connection are all synthetic.
"""
import ipaddress
import os
from pathlib import Path
import socket
import ssl

import pytest

from ir_search import RequestContext
from ir_search.infrastructure import public_web as web
from ir_search.infrastructure.recovery import _recovery, _stop_source
from ir_search.models import FailureKind
from ir_search.registry import DataAdapterError

KEY = "PUBLIC_WEB_FAKE_IP_HTTPS"
PLACEHOLDER, PUBLIC = "198.18.1.4", "93.184.216.34"
PRIVATE = ["10.0.0.5", "127.0.0.1", "169.254.169.254", "192.168.1.1", "::1", "fc00::1", "::ffff:198.18.0.1"]


def row(ip, port=443):
    if ":" in ip:
        return (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, port, 0, 0))
    return (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))


def dns(monkeypatch, *ips):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [row(ip) for ip in ips])


def switch(value):
    """Give the opt-in the only way it can be given: one line in the private credentials file."""
    path = Path(os.environ["IR_SEARCH_CREDENTIALS_FILE"])  # the per-test file made by conftest
    path.write_text(f"{KEY}={value}\n", encoding="utf-8")
    path.chmod(0o600)


def code_of(call):
    with pytest.raises(DataAdapterError) as caught:
        call()
    return caught.value.code


class _Socket:
    def settimeout(self, seconds): assert seconds > 0
    def shutdown(self, *args): pass
    def close(self): pass


class _Connection:
    """Stands in for the pinned connection; records where it was told to connect."""
    made = []

    def __init__(self, host, port, *, address, tls, timeout):
        self.host, self.port, self.address, self.tls, self.sock = host, port, address, tls, _Socket()
        self.body = b"<p>public text</p>"
        _Connection.made.append(self)

    def connect(self): pass
    def request(self, method, target, body, headers): pass
    def getresponse(self): return self
    def close(self): pass
    status = 200
    def getheader(self, name, default=None): return {"Content-Type": "text/html"}.get(name, default)
    def read1(self, size):
        part, self.body = self.body[:size], self.body[size:]
        return part


@pytest.fixture
def connections(monkeypatch):
    _Connection.made = []
    monkeypatch.setattr(web, "_Connection", _Connection)
    return _Connection.made


# --- switch off: a placeholder answer stays blocked, now with a code that names the cause ---

@pytest.mark.parametrize("configured", [None, "false"])
@pytest.mark.parametrize("scheme,port", [("https", 443), ("http", 80)])
def test_switch_off_keeps_placeholder_answers_blocked(monkeypatch, connections, configured, scheme, port):
    if configured is not None:
        switch(configured)
    dns(monkeypatch, PLACEHOLDER)
    assert code_of(lambda: web._resolve("example.org", port, RequestContext(), scheme)) == "fake_ip_blocked"
    assert code_of(lambda: web._request(f"{scheme}://example.org/a", context=RequestContext())) == "fake_ip_blocked"
    assert connections == []


# --- switch on + https + every answer inside the block: let through, certificate check intact ---

def test_switch_on_lets_https_connect_to_the_placeholder_with_certificate_checks(monkeypatch, connections):
    switch("true")
    dns(monkeypatch, PLACEHOLDER, "198.19.255.255")
    assert web._resolve("example.org", 443, RequestContext(), "https") == row(PLACEHOLDER)
    reply = web._request("https://example.org/a", context=RequestContext())
    assert reply.status == 200 and reply.body == b"<p>public text</p>"
    (connection,) = connections
    assert connection.address == row(PLACEHOLDER) and (connection.host, connection.port) == ("example.org", 443)
    # The waiver rests on this: the peer still has to prove the name with a trusted certificate.
    assert connection.tls.check_hostname and connection.tls.verify_mode == ssl.CERT_REQUIRED
    assert connection.tls.minimum_version >= ssl.TLSVersion.TLSv1_2


@pytest.mark.parametrize("edge", ["198.18.0.0", "198.19.255.255"])
def test_both_ends_of_the_block_are_placeholders(monkeypatch, edge):
    dns(monkeypatch, edge)
    assert code_of(lambda: web._resolve("example.org", 443, RequestContext(), "https")) == "fake_ip_blocked"
    switch("true")
    assert web._resolve("example.org", 443, RequestContext(), "https") == row(edge)


# --- switch on + plain http: still blocked ---

def test_switch_on_never_covers_plain_http(monkeypatch, connections):
    switch("true")
    dns(monkeypatch, PLACEHOLDER)
    assert code_of(lambda: web._resolve("example.org", 80, RequestContext(), "http")) == "fake_ip_blocked"
    assert code_of(lambda: web._request("http://example.org/a", context=RequestContext())) == "fake_ip_blocked"
    assert connections == []


def test_a_caller_that_states_no_scheme_gets_no_waiver(monkeypatch):
    switch("true")
    dns(monkeypatch, PLACEHOLDER)
    assert code_of(lambda: web._resolve("example.org", 443, RequestContext())) == "fake_ip_blocked"


def test_request_states_the_scheme_of_the_url_it_was_given(monkeypatch):
    seen = []
    def record(*args):
        seen.append(args[1:2] + args[3:])
        raise DataAdapterError("network")
    monkeypatch.setattr(web, "_resolve", record)
    for url in ("https://example.org/a", "http://example.org/a"):
        assert code_of(lambda: web._request(url, context=RequestContext())) == "network"
    assert seen == [(443, "https"), (80, "http")]


# --- switch on + a placeholder next to a non-public address: the whole answer is refused ---

@pytest.mark.parametrize("private", PRIVATE)
@pytest.mark.parametrize("placeholder_first", [True, False])
def test_switch_on_mixed_answer_is_blocked_as_a_whole(monkeypatch, connections, private, placeholder_first):
    switch("true")
    dns(monkeypatch, *((PLACEHOLDER, private) if placeholder_first else (private, PLACEHOLDER)))
    assert code_of(lambda: web._resolve("example.org", 443, RequestContext(), "https")) == "blocked_url"
    assert code_of(lambda: web._request("https://example.org/a", context=RequestContext())) == "blocked_url"
    assert connections == []


# --- switch on + the placeholder written into the URL itself: refused before any DNS ---

@pytest.mark.parametrize("url", ["https://198.18.0.5/", "https://198.18.0.5/a?b=c", "https://198.19.255.255/"])
def test_switch_on_literal_placeholder_in_the_url_is_still_blocked(monkeypatch, connections, url):
    switch("true")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: pytest.fail("a literal address needs no DNS"))
    assert code_of(lambda: web._url(url)) == "blocked_url"
    assert code_of(lambda: web._request(url, context=RequestContext())) == "blocked_url"
    assert connections == []


# --- switch on + ordinary public answers: exactly the behaviour without this feature ---

@pytest.mark.parametrize("configured", [None, "false", "true", "not-a-literal"])
def test_public_answers_behave_as_before_and_never_consult_the_switch(monkeypatch, configured):
    if configured is not None:
        switch(configured)
    monkeypatch.setattr(web, "_fake_ip_https_enabled", lambda: pytest.fail("no placeholder, nothing to decide"))
    dns(monkeypatch, PUBLIC, "198.17.255.255", "198.20.0.0")  # the block's neighbours are ordinary public space
    for scheme, port in (("https", 443), ("http", 80), (None, 443)):
        assert web._resolve("example.org", port, RequestContext(), scheme) == row(PUBLIC)
    for private in PRIVATE:
        dns(monkeypatch, PUBLIC, private)
        assert code_of(lambda: web._resolve("example.org", 443, RequestContext(), "https")) == "blocked_url"


def test_switch_on_private_answers_alone_are_blocked_as_before(monkeypatch):
    switch("true")
    for private in PRIVATE:
        dns(monkeypatch, private)
        assert code_of(lambda: web._resolve("example.org", 443, RequestContext(), "https")) == "blocked_url"


# --- the switch itself: one exact literal, one place, one fixed block ---

@pytest.mark.parametrize("value", ["TRUE", "True", "1", "yes", "on", "https", "https_only", ""])
def test_any_value_but_the_exact_literal_is_a_configuration_error(monkeypatch, connections, value):
    switch(value)
    dns(monkeypatch, PLACEHOLDER)
    assert code_of(lambda: web._resolve("example.org", 443, RequestContext(), "https")) == "source_config_error"
    assert code_of(lambda: web._request("https://example.org/a", context=RequestContext())) == "source_config_error"
    assert connections == []


def test_an_unreadable_credentials_file_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("IR_SEARCH_CREDENTIALS_FILE", str(tmp_path / "missing.env"))
    dns(monkeypatch, PLACEHOLDER)
    assert code_of(lambda: web._resolve("example.org", 443, RequestContext(), "https")) == "source_config_error"


def test_the_process_environment_cannot_turn_it_on(monkeypatch):
    monkeypatch.setenv(KEY, "true")
    dns(monkeypatch, PLACEHOLDER)
    assert code_of(lambda: web._resolve("example.org", 443, RequestContext(), "https")) == "fake_ip_blocked"


def test_the_block_is_a_constant_that_configuration_cannot_widen(monkeypatch):
    assert web._FAKE_IP_BLOCK == ipaddress.ip_network("198.18.0.0/15")
    path = Path(os.environ["IR_SEARCH_CREDENTIALS_FILE"])
    path.write_text(f"{KEY}=true\nPUBLIC_WEB_FAKE_IP_BLOCK=10.0.0.0/8\nPUBLIC_WEB_FAKE_IP_RANGES=10.0.0.0/8\n", encoding="utf-8")
    path.chmod(0o600)
    dns(monkeypatch, "10.0.0.5")
    assert code_of(lambda: web._resolve("example.org", 443, RequestContext(), "https")) == "blocked_url"


# --- the new code and its guidance ---

def test_new_code_is_a_policy_block_with_guidance_of_its_own():
    assert DataAdapterError("fake_ip_blocked").failure_kind is FailureKind.BLOCKED_BY_POLICY
    assert DataAdapterError("blocked_url").failure_kind is FailureKind.BLOCKED_BY_POLICY
    guidance, generic = _recovery("fake_ip_blocked"), _recovery("blocked_url")
    assert guidance["category"] == generic["category"] == "transport_policy"
    assert guidance["next_action"] != generic["next_action"]
    for expected in (KEY + "=true", "fake-ip", "198.18.0.0/15", "HTTPS", "http", "不是目标站点的问题"):
        assert expected in guidance["next_action"]
    assert _stop_source("fake_ip_blocked") and _stop_source("blocked_url")


def test_guidance_says_what_the_switch_covers_and_names_what_it_does_not():
    text = _recovery("fake_ip_blocked")["next_action"]
    covered, _, not_covered = text.partition("不适用该开关")
    assert not_covered, "the text must separate what the switch cannot help with"
    for expected in ("HTTPS/WSS", "证书与主机名校验", "浏览器模式", "语音转写", "Firecrawl",
                     "雪球浏览器读取", "5 个固定域名", "443", "Playwright 自带的 Chromium", "端到端"):
        assert expected in covered
    for expected in ("明文 http", "系统 Chrome", "仍报本错误码"):
        assert expected in not_covered
    # The remedy stays inside ir-search: one key in the private credentials file, nothing else.
    assert text.count(KEY) == 1 and "credentials.env" in covered


def test_example_file_documents_the_switch_and_ships_it_off():
    text = (Path(__file__).resolve().parents[1] / "credentials.env.example").read_text(encoding="utf-8")
    assert f"\n{KEY}=false\n" in text and f"{KEY}=true" not in text
    comment = text[:text.index(f"\n{KEY}=false")].rsplit("WEB_ALLOWED_DOMAINS=", 1)[1]
    for expected in ("fake-ip", "198.18.0.0/15", "HTTPS", "Plain http", "fake_ip_blocked", "WSS", "speech",
                     "Firecrawl", "Xueqiu", "five fixed hosts", "port 443", "Chromium", "XUEQIU_BROWSER_CHANNEL=chrome"):
        assert expected in comment
