from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError
import json
import os
from pathlib import Path
import sys

import pytest

from ir_search import RequestContext
from ir_search.context import RequestStopped
from ir_search.infrastructure import _browser_network as net, web_browser as browser
from ir_search.infrastructure._crawl_worker import _launch_options
from ir_search.infrastructure.public_web import _Reply
from ir_search.registry import DataAdapterError

URL = "https://investor.apple.com/investor-relations/"
NOW = datetime.now(timezone.utc)


def network(): return net.BrowserNetwork(URL, RequestContext(max_operations=100))


@pytest.mark.parametrize("url,method", [("http://public.test/a", "GET"), ("https://127.0.0.1/a", "GET"),
    ("https://example.test:444/a", "GET"), ("file:///private/file", "GET"),
    ("https://example.test/a?apiKey=private", "GET"), ("https://example.test/a", "POST"),
    ("https://investor.apple.com/delete?apiKey=private", "POST"),
    ("https://investor.apple.com/feed/FinancialReport.svc/GetFinancialReportList?apiKey=public", "DELETE")])
def test_browser_rejects_unsafe_urls_and_mutations_before_transport(monkeypatch, url, method):
    monkeypatch.setattr(net, "_request", lambda *a, **kw: pytest.fail("forbidden request"))
    with pytest.raises(DataAdapterError, match="blocked_url"): network().fetch(url, method=method)


def test_only_fixed_public_same_origin_read_feeds_can_use_site_parameters(monkeypatch):
    calls = []
    def transport(url, **kw):
        calls.append((url, kw))
        return _Reply(200, "application/json", "", b'{}', NOW)
    monkeypatch.setattr(net, "_request", transport)
    url = "https://investor.apple.com/feed/FinancialReport.svc/GetFinancialReportList?apiKey=public_site_parameter"
    network().fetch(url, method="POST", body=b'{"reportYear":2026}')
    assert calls[0][1]["public_feed"] is True
    assert calls[0][1]["headers"] == {"Content-Type": "application/json"}
    other = net.BrowserNetwork("https://evil.test/a", RequestContext())
    with pytest.raises(DataAdapterError, match="blocked_url"): other.fetch(url)
    with pytest.raises(DataAdapterError, match="blocked_url"): network().fetch(url, main=True)


@pytest.mark.parametrize("target", ["https://127.0.0.1/private", "http://public.test/", "https://public.test/?token=secret"])
def test_browser_checks_redirect_targets(monkeypatch, target):
    monkeypatch.setattr(net, "_request", lambda *a, **k: _Reply(302, "", target, b"", NOW))
    with pytest.raises(DataAdapterError, match="blocked_url"): network().fetch("https://public.test/a.js")


def test_robots_denial_and_main_domain_restrictions(monkeypatch):
    calls = []
    def transport(url, **kw):
        calls.append(url)
        return _Reply(200, "text/plain", "", b"User-agent: *\nDisallow: /", NOW)
    monkeypatch.setattr(net, "_request", transport)
    with pytest.raises(DataAdapterError, match="browser_robots_denied"): network().fetch(URL, main=True)
    assert calls == ["https://investor.apple.com/robots.txt"]
    n = net.BrowserNetwork(URL, RequestContext(), ("example.gov.cn",))
    with pytest.raises(DataAdapterError, match="blocked_url"): n.fetch(URL, main=True)


def test_byte_budget_cors_headers_and_diagnostics(monkeypatch):
    def transport(url, **kw):
        kw["context"].begin_operation()
        assert kw["preserve_headers"] is True and kw["max_bytes"] == 3
        return _Reply(200, "application/javascript", "", b"abc", NOW,
                      {"Content-Type": "application/javascript", "Access-Control-Allow-Origin": "*"})
    monkeypatch.setattr(net, "_request", transport)
    n = network(); n.max_bytes = 3
    status, headers, raw = n.fetch("https://cdn.test/app.js")
    assert raw == b"abc" and headers["Access-Control-Allow-Origin"] == "*"
    with pytest.raises(DataAdapterError, match="browser_budget_exhausted"): n.fetch("https://cdn.test/app.js")
    assert n.diagnostics()["received_bytes"] == 3 and n.context.operations == 1


def test_worker_environment_drops_credentials_dotenv_proxies_and_python_hooks(monkeypatch):
    for key in ("WIND_PASSWORD", "OPENAI_API_KEY", "HTTPS_PROXY", "HTTP_PROXY", "PYTHONPATH", "NODE_OPTIONS", "IR_SEARCH_CREDENTIALS_FILE"):
        monkeypatch.setenv(key, "must_not_escape")
    env = browser._environment("/tmp/fixture")
    assert "must_not_escape" not in json.dumps(env)
    assert env["PYTHON_DOTENV_DISABLED"] == "1" and env["LITELLM_LOCAL_MODEL_COST_MAP"] == "True"


def test_launch_options_do_not_inherit_certificate_or_sandbox_bypasses():
    config = _launch_options("http://127.0.0.1:12345")
    assert config["chromium_sandbox"] is True and config["proxy"]["bypass"] == "<-loopback>"
    assert not any("ignore-certificate" in a or a == "--no-sandbox" for a in config["args"])


def test_missing_and_unknown_crawl_versions_do_not_launch(monkeypatch):
    monkeypatch.setattr(browser, "_run_worker", lambda *a, **k: pytest.fail("unexpected launch"))
    def missing(name): raise PackageNotFoundError(name)
    monkeypatch.setattr(browser, "version", missing)
    with pytest.raises(DataAdapterError, match="browser_dependency_missing"):
        browser.render_public_document(URL, context=RequestContext())
    monkeypatch.setattr(browser, "version", lambda _: "99.0")
    with pytest.raises(DataAdapterError, match="browser_version_unsupported"):
        browser.render_public_document(URL, context=RequestContext())


def test_worker_document_is_unfiltered_and_credential_free(monkeypatch):
    monkeypatch.setattr(browser, "version", lambda _: "0.9.3")
    monkeypatch.setattr(browser.sys, "version_info", (3, 12))
    monkeypatch.setattr(browser, "_run_worker", lambda *a, **k: {"html": "<p>收入 -3.20 亿元。未经审计。</p>",
        "url": URL, "diagnostics": {"blocked_requests": 2}})
    document = browser.render_public_document(URL, context=RequestContext())
    assert "未经审计" in document.text and document.extraction_method == "crawl4ai_render_stdlib_html_parser"
    assert "web_browser_requests_blocked" in document.warnings
    monkeypatch.setattr(browser, "_run_worker", lambda *a, **k: {"html": "", "url": "https://example.test/?token=private"})
    with pytest.raises(DataAdapterError, match="blocked_url"):
        browser.render_public_document(URL, context=RequestContext())


def test_raw_render_result_retains_source_html_for_specialized_parsers(monkeypatch):
    monkeypatch.setattr(browser, 'version', lambda _: '0.9.3')
    monkeypatch.setattr(browser.sys, 'version_info', (3, 12))
    html = '<script>var ct="1789472485";</script><div id="js_content">正文</div>'
    monkeypatch.setattr(browser, '_run_worker', lambda *a, **k: {'url':URL, 'html':html, 'diagnostics':{'blocked_requests':0}})
    page = browser.render_public_html(URL, context=RequestContext())
    assert page.html == html and page.url == URL and page.fetched_at.utcoffset() is not None
    assert html not in repr(page)
    def bad_parser(*a, **k): raise ValueError('private upstream detail')
    monkeypatch.setattr(browser, 'extract_html_document', bad_parser)
    with pytest.raises(DataAdapterError, match='browser_failed') as error:
        browser.render_public_document(URL, context=RequestContext())
    assert 'private' not in str(error.value)


def test_cancellation_reaps_worker_and_accounts_unknown_consumption(monkeypatch):
    class Input:
        def write(self, value): pass
        def close(self): pass
    class Process:
        stdin = Input()
        def poll(self): return None
    process = Process(); stopped = []
    ctx = RequestContext(max_operations=10)
    def launch(*a, **kw):
        ctx.cancel()
        assert kw["start_new_session"] == (os.name == "posix")
        return process
    monkeypatch.setattr(browser.subprocess, "Popen", launch)
    monkeypatch.setattr(browser, "_stop", lambda p: stopped.append(p))
    with pytest.raises(RequestStopped, match="cancelled"):
        browser._run_worker(URL, context=ctx, allowed_domains=())
    assert stopped == [process] and ctx.operations == 10


def test_worker_success_operation_accounting(monkeypatch):
    class Input:
        def write(self, value):
            payload = json.loads(value)
            Path(payload["output"]).write_text(json.dumps({"operations": 3, "html": "<p>Text</p>", "url": URL}))
        def close(self): pass
    class Process:
        stdin = Input()
        def poll(self): return 0
    monkeypatch.setattr(browser.subprocess, "Popen", lambda *a, **kw: Process())
    monkeypatch.setattr(browser, "_stop", lambda p: None)
    ctx = RequestContext(max_operations=10)
    result = browser._run_worker(URL, context=ctx, allowed_domains=())
    assert result["url"] == URL and ctx.operations == 4


def test_capability_metadata_is_not_a_browser_launch_or_live_health_claim(monkeypatch):
    from ir_search import list_capabilities
    from ir_search.registry import DataRegistry
    from ir_search.material_registry import MaterialRegistry
    monkeypatch.setattr(browser, "_run_worker", lambda *a, **k: pytest.fail("metadata must not launch"))
    result = list_capabilities(registry=DataRegistry(), material_registry=MaterialRegistry())["web_reading"]
    assert result["modes"] == ["http", "auto", "browser", "scrapling", "firecrawl"]
    assert result["browser"]["runtime_verified"] is False


def test_nvidia_public_post_is_read_only_and_same_origin(monkeypatch):
    calls = []
    def transport(url, **kw):
        calls.append(kw)
        return _Reply(200, "application/json", "", b'{}', NOW)
    monkeypatch.setattr(net, "_request", transport)
    n = net.BrowserNetwork("https://investor.nvidia.com/financial-info/financial-reports/default.aspx", RequestContext())
    n.fetch("https://investor.nvidia.com/Services/FinancialReportService.svc/GetFinancialReportList", method="POST", body=b'{}')
    assert calls[0]["public_feed"]
    with pytest.raises(DataAdapterError, match="blocked_url"):
        n.fetch("https://investor.nvidia.com/Services/FinancialReportService.svc/DeleteFinancialReport", method="POST", body=b'{}')
