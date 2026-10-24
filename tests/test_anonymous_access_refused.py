"""A 403 on a request that carries no credential is a refusal of an anonymous client, not an entitlement.

Offline: a plain HTTP server on 127.0.0.1 answers with a fixed status and the resolver is pointed at it.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import threading

import pytest

from ir_search import DataRegistry, DataRequest, RequestContext, get_data
from ir_search.adapters.global_macro import GlobalMacroAdapter
from ir_search.infrastructure import _browser_network as net, public_web as web, rss
from ir_search.infrastructure.recovery import _recovery, _stop_source
from ir_search.models import FailureKind
from ir_search.registry import DataAdapterError


@pytest.fixture
def site(monkeypatch):
    """A loopback site answering every request with one status; records what it was sent."""
    state = {"status": 403, "seen": []}

    class Handler(BaseHTTPRequestHandler):
        def answer(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            state["seen"].append((self.command, self.path, {k.lower(): v for k, v in self.headers.items()}))
            body = b"<html><body>refused</body></html>"
            self.send_response(state["status"])
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        do_GET = do_POST = answer
        def log_message(self, *args): pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    address = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", server.server_port))
    monkeypatch.setattr(web, "_resolve", lambda *args: address)
    yield state
    server.shutdown()
    server.server_close()


def code_of(call):
    with pytest.raises(DataAdapterError) as caught:
        call()
    return caught.value.code


def get(url="http://example.org/a", **options):
    return web._request(url, context=RequestContext(timeout_seconds=5), **options)


# --- the statement decides, and only for 403 ---

def test_a_403_is_refused_access_only_when_the_caller_states_the_request_is_anonymous(site):
    assert code_of(lambda: get(anonymous=True)) == "anonymous_access_refused"
    assert code_of(get) == "entitlement_denied"                      # nothing stated: exactly as before
    assert code_of(lambda: get(anonymous=False)) == "entitlement_denied"
    assert code_of(lambda: get(headers={"Authorization": "Bearer synthetic"})) == "entitlement_denied"
    assert [method for method, _, _ in site["seen"]] == ["GET"] * 4


@pytest.mark.parametrize("status,code", [(401, "authentication_failed"), (402, "entitlement_denied"), (404, "not_found"),
                                         (429, "rate_limit"), (500, "network"), (451, "network")])
def test_every_other_status_keeps_its_code_for_an_anonymous_request(site, status, code):
    site["status"] = status
    assert code_of(lambda: get(anonymous=True)) == code == code_of(get)


def test_the_headers_an_anonymous_request_may_carry_reach_the_site(site):
    code_of(lambda: get(anonymous=True, headers={"Accept": "text/csv", "User-Agent": "Python-urllib/3.12", "Accept-Language": "en"}))
    sent = site["seen"][-1][2]
    assert sent["accept"] == "text/csv" and sent["user-agent"] == "Python-urllib/3.12" and "authorization" not in sent


# --- a statement the transport can see to be false is refused before any connection ---

@pytest.mark.parametrize("options", [
    dict(headers={"Authorization": "Bearer synthetic"}), dict(headers={"Cookie": "a=b"}), dict(headers={"X-Api-Key": "k"}),
    dict(headers={"Referer": "https://example.org/"}), dict(headers={"Content-Type": "application/json"}),
    dict(method="POST", body=b"{}"), dict(method="POST"), dict(body=b"x"), dict(method="HEAD"),
    dict(signed_download=True), dict(scoped_download_headers={"Authorization": "q-sign"}, signed_download=True),
    dict(public_feed=True), dict(anonymous=1), dict(anonymous="true"), dict(anonymous=None),
], ids=lambda options: ",".join(sorted(options)) + "=" + str(next(iter(options.values())))[:18])
def test_a_false_or_malformed_statement_is_refused_without_any_request(site, options):
    options.setdefault("anonymous", True)
    url = "https://example.org/a" if options.get("signed_download") or options.get("public_feed") else "http://example.org/a"
    assert code_of(lambda: get(url, **options)) == "blocked_url" and site["seen"] == []


def test_a_fixed_public_feed_cannot_be_stated_anonymous_even_as_a_plain_get(site):
    """A valid feed URL, so that nothing but the statement check can be what refuses it."""
    feed = "https://investor.apple.com/feed/Event.svc/GetEventYearList"
    assert code_of(lambda: get(feed, public_feed=True, anonymous=True)) == "blocked_url" and site["seen"] == []


def test_search_api_error_replies_cannot_be_combined_with_the_statement(site):
    """A search API call is always a POST with a key, which already contradicts the statement."""
    options = dict(method="POST", body=b"{}", search_api_errors=True, anonymous=True)
    assert code_of(lambda: get("https://api.exa.ai/search", **options)) == "blocked_url" and site["seen"] == []


# --- the first callers that make the statement ---

def test_public_page_reads_state_it(site):
    assert code_of(lambda: web.fetch_public_document("http://example.org/a", context=RequestContext(timeout_seconds=5))) == "anonymous_access_refused"


def test_public_feed_reads_state_it(monkeypatch):
    stated = []
    def request(url, **options):
        stated.append((options.get("anonymous"), sorted(options.get("headers") or {})))
        raise DataAdapterError("anonymous_access_refused")
    monkeypatch.setattr(rss, "_request", request)
    assert code_of(lambda: rss.fetch_feed("https://example.org/feed.xml", context=RequestContext())) == "anonymous_access_refused"
    assert stated == [(True, ["Accept"])]


def test_macro_series_state_it_and_the_series_row_shows_the_refusal():
    stated = []
    def transport(url, **options):
        stated.append((options.get("anonymous"), sorted(options.get("headers") or {})))
        if "fred" in url:
            raise DataAdapterError("anonymous_access_refused")
        body = b"KEY,TIME_PERIOD,OBS_VALUE,UNIT,UNIT_MULT,OBS_STATUS\nEXR.D.USD.EUR.SP00.A,2025-01-02,1.03,USD,0,A\n"
        return web._Reply(200, "text/csv", "", body, __import__("datetime").datetime.now(__import__("datetime").timezone.utc))
    registry = DataRegistry()
    registry.register(GlobalMacroAdapter(transport=transport))
    request = DataRequest("macro_series", market="GLOBAL", symbols=["FRED.DGS10", "ECB.EXR.D.USD.EUR.SP00.A"],
                          start="2025-01-01", end="2025-01-31")
    result = get_data(request, registry=registry)
    assert stated == [(True, ["Accept", "User-Agent"]), (True, ["Accept"])]
    codes = [d.code for d in result.diagnostics]
    assert "anonymous_access_refused" in codes and "entitlement_denied" not in codes
    assert [row["symbol"] for row in result.records] == ["ECB.EXR.D.USD.EUR.SP00.A"]  # the other series is intact


def test_the_headers_the_macro_adapter_sends_are_accepted_by_the_real_transport(site):
    """FRED needs a User-Agent; the statement must survive the transport's own check of it."""
    headers = {"Accept": "application/json,text/csv", "User-Agent": "Python-urllib/3.12"}
    assert code_of(lambda: get(anonymous=True, headers=headers)) == "anonymous_access_refused"


def test_browser_mode_states_it_for_plain_reads_and_robots_but_not_for_public_feeds(monkeypatch):
    stated = []
    def request(url, **options):
        stated.append((url.rsplit("/", 1)[-1], options.get("method", "GET"), options.get("anonymous")))
        raise DataAdapterError("not_found")
    monkeypatch.setattr(net, "_request", request)
    network = net.BrowserNetwork("https://example.org/a", RequestContext(), ())
    with pytest.raises(DataAdapterError):
        network.fetch("https://example.org/a", main=True)
    assert stated == [("robots.txt", "GET", True), ("a", "GET", True)]
    monkeypatch.setattr(network, "_validate", lambda url, method, main: True)  # a fixed public feed
    with pytest.raises(DataAdapterError):
        network.fetch("https://example.org/feed", method="POST", body=b"{}")
    assert stated[-1] == ("feed", "POST", False)


# --- what the code means ---

def test_the_code_is_a_policy_block_that_stops_the_source_unlike_an_entitlement():
    assert DataAdapterError("anonymous_access_refused").failure_kind is FailureKind.BLOCKED_BY_POLICY
    assert DataAdapterError("entitlement_denied").failure_kind is FailureKind.BLOCKED_BY_POLICY  # unchanged
    assert _stop_source("anonymous_access_refused") and not _stop_source("entitlement_denied")
    text = _recovery("anonymous_access_refused")["next_action"]
    assert text != _recovery("entitlement_denied")["next_action"]
    for expected in ("403", "不是权限不足", "不作推测", "不重试", "不尝试任何绕过", "如实记录"):
        assert expected in text
    assert "代理" not in text and "proxy" not in text.lower()
    assert json.dumps(_recovery("entitlement_denied"), ensure_ascii=False).count("权限不足") == 1
