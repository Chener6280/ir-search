"""Callers wired to the fake-ip opt-in, and the closed set of schemes that may use it.

No real network: DNS answers, the credentials file, the cloud API and the websocket are synthetic.
"""
from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path
import socket
import ssl
import struct
import sys
from types import SimpleNamespace

import pytest

from ir_search import RequestContext
from ir_search.infrastructure import audio_asr as asr, public_web as web, web_toolkit as toolkit
from ir_search.infrastructure.credentials import SourceConfigError
from ir_search.registry import DataAdapterError

KEY = "PUBLIC_WEB_FAKE_IP_HTTPS"
PLACEHOLDER, PUBLIC = "198.18.1.4", "93.184.216.34"


def row(ip, port=443):
    return (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))


def switch(value):
    path = Path(os.environ["IR_SEARCH_CREDENTIALS_FILE"])  # the per-test file made by conftest
    path.write_text(f"{KEY}={value}\n", encoding="utf-8")
    path.chmod(0o600)


def dns(monkeypatch, table):
    """Answer per host; a host that is not in the table must never be looked up."""
    def lookup(host, port, *args, **kwargs):
        if host not in table:
            pytest.fail(f"unexpected DNS question for {host}")
        return [row(ip, port) for ip in table[host]]
    monkeypatch.setattr(socket, "getaddrinfo", lookup)


def code_of(call):
    with pytest.raises(DataAdapterError) as caught:
        call()
    return caught.value.code


# --- the closed set in _resolve: exactly https and wss, nothing that merely looks like them ---

@pytest.mark.parametrize("scheme", ["https", "wss"])
def test_the_two_verified_tls_schemes_pass_a_placeholder_only_with_the_switch_on(monkeypatch, scheme):
    dns(monkeypatch, {"example.org": [PLACEHOLDER]})
    assert code_of(lambda: web._resolve("example.org", 443, RequestContext(), scheme)) == "fake_ip_blocked"
    switch("true")
    assert web._resolve("example.org", 443, RequestContext(), scheme) == row(PLACEHOLDER)


@pytest.mark.parametrize("scheme", ["ws", "http", None, "HTTPS", "WSS", "Https", "Wss", "wSs", "https ", " wss", "wss:",
                                    "https://", "httpss", "wsss", "h2", "tls", "ftp", "", b"https", b"wss", 443, True])
def test_every_other_scheme_value_is_refused_with_the_switch_on(monkeypatch, scheme):
    switch("true")
    dns(monkeypatch, {"example.org": [PLACEHOLDER]})
    assert code_of(lambda: web._resolve("example.org", 443, RequestContext(), scheme)) == "fake_ip_blocked"


@pytest.mark.parametrize("private", ["10.0.0.5", "127.0.0.1", "169.254.169.254", "::1"])
def test_wss_does_not_carry_a_private_address_in_with_a_placeholder(monkeypatch, private):
    switch("true")
    dns(monkeypatch, {"example.org": [PLACEHOLDER, private]})
    assert code_of(lambda: web._resolve("example.org", 443, RequestContext(), "wss")) == "blocked_url"


# --- Firecrawl: both local checks state the scheme of the URL they are checking ---

HTML = "<title>Annual report</title><article><p>" + "Revenue 123.45, unaudited. " * 10 + "</p></article>"
PROFILE = toolkit.FirecrawlProfile("test-private-key")


@pytest.fixture
def cloud(monkeypatch):
    """The cloud API answers with whatever final URL the test sets; no request leaves the process."""
    state = SimpleNamespace(calls=[], final_url=None)
    def send(url, **kwargs):
        kwargs["context"].begin_operation()
        state.calls.append(url)
        data = {"rawHtml": HTML, "metadata": {"url": state.final_url, "statusCode": 200}}
        return web._Reply(200, "application/json", "", json.dumps({"success": True, "data": data}).encode(),
                          datetime.now(timezone.utc))
    monkeypatch.setattr(web, "_request", send)
    monkeypatch.setattr(web, "_Connection", lambda *a, **kw: pytest.fail("the target is never connected to locally"))
    return state


def scrape(url):
    return toolkit.fetch_firecrawl_document(url, context=RequestContext(), profile=PROFILE)


def test_firecrawl_https_target_behind_placeholders_needs_the_switch(monkeypatch, cloud):
    cloud.final_url = "https://example.com/report"
    dns(monkeypatch, {"example.com": [PLACEHOLDER]})
    assert code_of(lambda: scrape("https://example.com/report")) == "fake_ip_blocked"
    assert cloud.calls == []  # refused before anything is sent to the cloud
    switch("true")
    document = scrape("https://example.com/report")
    assert "Revenue 123.45" in document.text and cloud.calls == ["https://api.firecrawl.dev/v2/scrape"]


def test_firecrawl_plain_http_target_is_never_waived(monkeypatch, cloud):
    switch("true")
    cloud.final_url = "http://example.com/report"
    dns(monkeypatch, {"example.com": [PLACEHOLDER]})
    assert code_of(lambda: scrape("http://example.com/report")) == "fake_ip_blocked"
    assert cloud.calls == []


def test_firecrawl_reported_final_http_url_is_never_waived(monkeypatch, cloud):
    """The second check states the reported URL's own scheme, not the scheme of the request."""
    switch("true")
    cloud.final_url = "http://moved.example.net/report"
    dns(monkeypatch, {"plain.example.com": [PUBLIC], "moved.example.net": [PLACEHOLDER]})
    assert code_of(lambda: scrape("http://plain.example.com/report")) == "fake_ip_blocked"
    assert len(cloud.calls) == 1


def test_firecrawl_reported_final_https_url_follows_the_switch(monkeypatch, cloud):
    cloud.final_url = "https://moved.example.net/report"
    dns(monkeypatch, {"example.com": [PUBLIC], "moved.example.net": [PLACEHOLDER]})
    assert code_of(lambda: scrape("https://example.com/report")) == "fake_ip_blocked"
    switch("true")
    assert scrape("https://example.com/report").url == "https://moved.example.net/report"


def test_firecrawl_http_request_upgraded_to_https_is_judged_by_the_reported_scheme(monkeypatch, cloud):
    switch("true")
    cloud.final_url = "https://moved.example.net/report"
    dns(monkeypatch, {"plain.example.com": [PUBLIC], "moved.example.net": [PLACEHOLDER]})
    assert scrape("http://plain.example.com/report").url == "https://moved.example.net/report"


@pytest.mark.parametrize("switched_on,first_answer", [(True, PLACEHOLDER), (False, PUBLIC)])
def test_firecrawl_https_to_http_downgrade_is_refused_exactly_as_before(monkeypatch, cloud, switched_on, first_answer):
    if switched_on:
        switch("true")
    cloud.final_url = "http://example.com/report"
    dns(monkeypatch, {"example.com": [first_answer]})  # asked once, for the request; never for the downgraded URL
    asked = []
    original = socket.getaddrinfo
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **kw: asked.append(host) or original(host, *a, **kw))
    assert code_of(lambda: scrape("https://example.com/report")) == "blocked_url"
    assert asked == ["example.com"] and len(cloud.calls) == 1


def test_firecrawl_placeholder_next_to_a_private_answer_is_refused_as_a_whole(monkeypatch, cloud):
    switch("true")
    cloud.final_url = "https://moved.example.net/report"
    dns(monkeypatch, {"example.com": [PLACEHOLDER], "moved.example.net": [PLACEHOLDER, "10.0.0.5"]})
    assert code_of(lambda: scrape("https://example.com/report")) == "blocked_url"


# --- speech recognition: the websocket states wss and pins its own TLS floor ---

def frame(payload):
    raw = gzip.compress(json.dumps(payload).encode())
    return bytes([0x11, (9 << 4) | 3, 0x11, 0]) + struct.pack(">i", -2) + struct.pack(">I", len(raw)) + raw


@pytest.fixture
def speech(monkeypatch):
    seen = SimpleNamespace(connected=[], websocket=[])

    class Socket:
        def __init__(self, *args): pass
        def settimeout(self, value): pass
        def connect(self, address): seen.connected.append(address)
        def shutdown(self, *args): pass
        def close(self): pass

    class WebSocket:
        socket = Socket()
        def send(self, payload): pass
        def recv(self, timeout): return frame({"result": {"text": "ok"}})
        def close(self): pass

    def connect(url, **kwargs):
        seen.websocket.append((url, kwargs))
        return WebSocket()

    monkeypatch.setitem(sys.modules, "websockets.sync.client", SimpleNamespace(connect=connect))
    monkeypatch.setitem(sys.modules, "websockets.exceptions", SimpleNamespace(InvalidStatus=type("InvalidStatus", (Exception,), {})))
    monkeypatch.setattr(asr.socket, "socket", Socket)
    return seen


def test_speech_states_wss_for_its_fixed_host(monkeypatch, speech, tmp_path):
    stated = []
    monkeypatch.setattr(asr, "_resolve", lambda *args: stated.append(args[:2] + args[3:]) or row(PUBLIC))
    assert asr._stream(b"\0\0", asr.AudioProfile("test-key", tmp_path), RequestContext())["result"]["text"] == "ok"
    assert stated == [("openspeech.bytedance.com", 443, "wss")]
    assert asr.ENDPOINT.startswith("wss://openspeech.bytedance.com/")
    with pytest.raises(SourceConfigError):  # the waiver rests on this endpoint staying a constant
        asr.AudioProfile("test-key", tmp_path, endpoint="wss://elsewhere.example/api")


def test_speech_sets_the_tls_floor_itself_instead_of_trusting_the_default(monkeypatch, speech, tmp_path):
    made = []
    def permissive_default():
        context = SimpleNamespace(minimum_version=ssl.TLSVersion.MINIMUM_SUPPORTED, check_hostname=True,
                                  verify_mode=ssl.CERT_REQUIRED)
        made.append(context)
        return context
    monkeypatch.setattr(asr.ssl, "create_default_context", permissive_default)
    monkeypatch.setattr(asr, "_resolve", lambda *args: row(PUBLIC))
    asr._stream(b"\0\0", asr.AudioProfile("test-key", tmp_path), RequestContext())
    ((url, options),) = speech.websocket
    assert options["ssl"] is made[0] and len(made) == 1
    assert options["ssl"].minimum_version == ssl.TLSVersion.TLSv1_2
    assert options["server_hostname"] == "openspeech.bytedance.com" and options["proxy"] is None


def test_speech_real_context_verifies_the_certificate_and_the_name(monkeypatch, speech, tmp_path):
    monkeypatch.setattr(asr, "_resolve", lambda *args: row(PUBLIC))
    asr._stream(b"\0\0", asr.AudioProfile("test-key", tmp_path), RequestContext())
    context = speech.websocket[0][1]["ssl"]
    assert isinstance(context, ssl.SSLContext) and context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2


def test_speech_behind_placeholders_follows_the_switch(monkeypatch, speech, tmp_path):
    """The real resolver runs; only the DNS answer is synthetic."""
    dns(monkeypatch, {"openspeech.bytedance.com": [PLACEHOLDER]})
    profile = asr.AudioProfile("test-key", tmp_path)
    assert code_of(lambda: asr._stream(b"\0\0", profile, RequestContext())) == "fake_ip_blocked"
    assert speech.connected == [] and speech.websocket == []
    switch("true")
    assert asr._stream(b"\0\0", profile, RequestContext())["result"]["text"] == "ok"
    assert speech.connected == [(PLACEHOLDER, 443)]
