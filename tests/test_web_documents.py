"""Known-answer fixtures, not live financial observations."""
from datetime import datetime, timezone
import hashlib
import json

import pytest

from ir_search import MaterialRequest, MaterialSearchRequest, RequestContext, retrieve, search_materials, MaterialRegistry
from ir_search.adapters.web_materials import WebMaterialAdapter
from ir_search.documents.html import extract_html_document, parse_datetime
from ir_search.evidence import extract_evidence
from ir_search.evidence.extractor import chunk_document_text
from ir_search.infrastructure import web_documents as web, web_browser
from ir_search.infrastructure.credentials import WebMaterialProfile
from ir_search.infrastructure.web_search import WebSearchPage
from ir_search.registry import DataAdapterError

URL = "https://example.gov.cn/report"


def doc(html, url=URL):
    result = extract_html_document(html.encode(), url, max_chars=100000)
    result.extra["adapter_mode"] = "live"
    return result


@pytest.mark.parametrize("size", [1, 3, 60, 120, 1200])
def test_citations_exact_offsets_long_markdown_chinese_repeated_segments(size):
    text = ("# Revenue 收入📊\n\n" + (" Revenue 收入 -3.20 亿元。\n  | Revenue | 未经审计 |\t " * 120) + "\n\n") * 2
    chunks = chunk_document_text(text, max_span_chars=size)
    assert chunks and all(text[c.start:c.end] == c.text and 0 < len(c.text) <= size for c in chunks)
    document = doc("<p>Revenue</p>")
    document.text = text
    spans = extract_evidence(document, "Revenue 收入", max_spans=50, max_span_chars=size)
    assert all(s.text == text[s.start_char:s.end_char] for s in spans)
    assert all(s.extra["text_hash"] == hashlib.sha256(text.encode()).hexdigest() for s in spans)


@pytest.mark.parametrize("size", [0, -1, True, 1.5])
def test_invalid_chunk_size(size):
    with pytest.raises(ValueError):
        chunk_document_text("revenue", max_span_chars=size)


@pytest.mark.parametrize("html,state", [
    ("<title>Report</title><p>Revenue 123.45亿元，未经审计，不含海外收入。</p>", "article_text"),
    ("<title>Just a moment</title><p>Verify you are human</p>", "challenge"),
    ("<p>CAPTCHA</p>", "challenge"),
    ("<p>Crawl4AI Error: This page is not fully supported.</p>", "extractor_error"),
    ("<p>Loading financial reports...</p>", "loading"),
    ("<script>render()</script>", "loading"),
    ("<title>一图读懂政策</title><img src='chart.png'>", "image_only"),
    ("", "empty"),
    ("<title>How CAPTCHA works</title><p>Revenue article " + "details " * 1000 + "</p>", "article_text"),
])
def test_content_state_before_truncation(monkeypatch, html, state):
    monkeypatch.setattr(web, "fetch_public_document", lambda *a, **k: doc(html))
    monkeypatch.setattr(web_browser, "render_public_document", lambda *a, **k: pytest.fail("No browser in HTTP mode"))
    result = web.read_web_document(URL, context=RequestContext(), mode="http", max_chars=1)
    assert result.extra["web_read"]["content_state"] == state and len(result.text) <= 1
    assert result.extra["web_read"]["text_hash"] == hashlib.sha256(result.text.encode()).hexdigest()


def test_static_auto_does_not_import_or_call_browser(monkeypatch):
    monkeypatch.setattr(web, "fetch_public_document", lambda *a, **k: doc("<p>Revenue 123.45亿元。</p>"))
    monkeypatch.setattr(web_browser, "render_public_document", lambda *a, **k: pytest.fail("unnecessary browser"))
    assert web.read_web_document(URL, context=RequestContext()).extra["web_read"]["backend"] == "http"


@pytest.mark.parametrize("code", ["tls_error", "blocked_url", "entitlement_denied", "rate_limit", "network", "timeout"])
def test_http_failures_never_trigger_browser(monkeypatch, code):
    def failure(*a, **k): raise DataAdapterError(code)
    monkeypatch.setattr(web, "fetch_public_document", failure)
    monkeypatch.setattr(web_browser, "render_public_document", lambda *a, **k: pytest.fail("forbidden fallback"))
    with pytest.raises(DataAdapterError, match=code):
        web.read_web_document(URL, context=RequestContext(), mode="browser")


@pytest.mark.parametrize("code", ["browser_dependency_missing", "browser_version_unsupported", "browser_unavailable"])
def test_missing_browser_keeps_loading_diagnostic_and_no_fake_text(monkeypatch, code):
    monkeypatch.setattr(web, "fetch_public_document", lambda *a, **k: doc("<p>Loading financial reports...</p>"))
    def missing(*a, **k): raise DataAdapterError(code)
    monkeypatch.setattr(web_browser, "render_public_document", missing)
    result = retrieve(MaterialRequest("reports", [URL]))
    assert not result.materials
    assert {"web_content_loading", code} <= {d.code for d in result.diagnostics}


def test_dynamic_render_retains_all_directory_links_and_numeric_qualifiers(monkeypatch):
    initial = '<title>Financial reports</title><p>Loading financial reports...</p>'
    html = '<title>Financial reports</title><p>收入 -3.20 亿元，未经审计，不含海外收入。</p>'
    html += ''.join(f'<a href="/q{i}.pdf">Call transcript {i}</a>' for i in range(65))
    html += '<a href="/q0.pdf#page=1">duplicate</a><a href="https://private.test/?token=secret">private</a>'
    monkeypatch.setattr(web, "fetch_public_document", lambda *a, **k: doc(initial))
    monkeypatch.setattr(web_browser, "render_public_document", lambda *a, **k: doc(html))
    result = retrieve(MaterialRequest("收入 transcript", [URL]))
    material = result.materials[0]
    assert material.read_details["backend"] == "crawl4ai" and material.read_details["content_state"] == "directory_links"
    assert len(material.links) == 65 and all(l["status"] == "discovered_not_retrieved" for l in material.links)
    assert "未经审计" in material.text and "secret" not in json.dumps(material.links)
    assert "text_truncated" not in material.warnings
    assert all(s["text"] == material.text[s["start_char"]:s["end_char"]] for s in material.evidence_spans)
    assert all(s["extra"]["text_hash"] == material.text_hash for s in material.evidence_spans)


def test_failed_render_does_not_promote_tool_error(monkeypatch):
    monkeypatch.setattr(web, "fetch_public_document", lambda *a, **k: doc("<p>Loading financial reports...</p>"))
    monkeypatch.setattr(web_browser, "render_public_document", lambda *a, **k: doc("<p>Crawl4AI Error: invalid tag</p>"))
    result = retrieve(MaterialRequest("reports", [URL]))
    assert not result.materials and result.diagnostics[0].code == "web_content_loading"


@pytest.mark.parametrize("value,expected", [("2026/02/28 09:30", "2026-02-28T09:30:00"),
    ("2026/02/28", "2026-02-28T00:00:00"), ("2026-02-28T09:30:00+08:00", "2026-02-28T09:30:00+08:00"),
    ("2026/02/31 09:30", None), ("Copyright 2026", None)])
def test_date_parsing_no_invented_timezones(value, expected):
    result = parse_datetime(value)
    assert (result.isoformat() if result else None) == expected


def test_metadata_conflict_is_explicit():
    document = doc('<meta name="pubdate" content="2026/02/28 09:30"><meta name="date" content="2026-03-01">')
    assert document.published_at == datetime(2026, 2, 28, 9, 30)
    assert document.extra["publication_metadata"]["timezone_known"] is False
    assert "publication_date_conflict" in document.warnings


def test_search_and_retrieve_use_same_reader_and_invalid_text_stays_snippet(monkeypatch):
    monkeypatch.setattr(web, "fetch_public_document", lambda *a, **k: doc("<title>Verify you are human</title>"))
    class Client:
        def search(self, *a, **kw):
            return WebSearchPage([{"url": URL, "title": "Revenue report", "snippet": "Revenue discovery snippet"}], datetime.now(timezone.utc))
    registry = MaterialRegistry()
    registry.register(WebMaterialAdapter(WebMaterialProfile(allow_anonymous=True), client=Client()))
    search = search_materials(MaterialSearchRequest("Revenue", providers=["web"], published_start="2026-09-01",
        published_end="2026-09-16", web_read_mode="http"), registry=registry)
    version = search.items[0]["versions"][0]
    assert version["text_scope"] == "search_snippet" and version["read_details"]["content_state"] == "challenge"
    for span in version["evidence_spans"]:
        source = version[span["source_part"]]
        assert span["text"] == source[span["start_char"]:span["end_char"]]
        assert span["source_part_hash"] == hashlib.sha256(source.encode()).hexdigest()
    result = retrieve(MaterialRequest("Revenue", [URL], web_read_mode="http"))
    assert not result.materials and "web_content_challenge" in {d.code for d in result.diagnostics}


@pytest.mark.parametrize("options", [{"mode": "invalid"}, {"max_chars": True}, {"allowed_domains": "gov.cn"}])
def test_read_validation(options):
    with pytest.raises(ValueError): web.read_web_document(URL, context=RequestContext(), **options)


def test_citation_mismatch_is_not_returned(monkeypatch):
    from ir_search.services import retrieval
    from dataclasses import replace
    document = doc("<p>Revenue increased</p>")
    spans = extract_evidence(document, "Revenue")
    monkeypatch.setattr(retrieval, "read_web_document", lambda *a, **k: document)
    monkeypatch.setattr(retrieval, "extract_evidence", lambda *a, **k: [replace(spans[0], start_char=1)])
    result = retrieve(MaterialRequest("Revenue", [URL]))
    assert not result.materials and result.diagnostics[-1].code == "citation_offset_mismatch"


def test_follow_links_single_level_explicit_domains_budget_and_change_detection(monkeypatch):
    pages = {URL: '<title>Revenue</title><a href="/report2">Revenue followup</a><a href="https://outside.test/a">Outside</a>',
             'https://example.gov.cn/report2': '<p>Revenue second page</p><a href="/report3">Revenue third</a>'}
    calls = []
    def reader(url, **kw):
        calls.append(url)
        return doc(pages[url], url)
    monkeypatch.setattr(web, "fetch_public_document", reader)
    options = dict(web_read_mode="http", follow_links=3, link_domains=["example.gov.cn"])
    first = retrieve(MaterialRequest("Revenue", [URL], **options))
    assert calls == [URL, 'https://example.gov.cn/report2'] and first.discovery["queued_links"] == 1
    assert first.discovery["max_depth"] == 1 and first.discovery["complete"] is False
    hashes = {m.url: m.text_hash for m in first.materials}
    second = retrieve(MaterialRequest("Revenue", [URL], previous_text_hashes=hashes, **options))
    assert all(m.read_details["change_state"] == "unchanged" for m in second.materials)
    pages[URL] += '<p>Revenue changed</p>'
    third = retrieve(MaterialRequest("Revenue", [URL], previous_text_hashes=hashes, **options))
    assert third.materials[0].read_details["change_state"] == "changed"
    assert third.materials[1].read_details["discovered_from"] == URL


@pytest.mark.parametrize("options", [{"follow_links": 1}, {"follow_links": 11}, {"follow_links": True},
    {"link_domains": ["*.test"]}, {"previous_text_hashes": {URL: "bad"}},
    {"previous_text_hashes": {"https://example.test/?token=secret": "a" * 64}}])
def test_follow_and_incremental_request_validation(options):
    with pytest.raises(ValueError): MaterialRequest("Revenue", [URL], **options)


def test_default_does_not_follow_and_followed_redirects_keep_domain_restriction(monkeypatch):
    from ir_search.services import retrieval
    calls = []
    document = web._annotate(doc('<p>Revenue</p><a href="/follow">Next</a>'), "http")
    def reader(url, **kw):
        calls.append((url, kw["allowed_domains"]))
        if url.endswith('/follow'):
            assert kw["allowed_domains"] == ('example.gov.cn',)
            raise DataAdapterError("blocked_url")
        return document
    monkeypatch.setattr(retrieval, "read_web_document", reader)
    retrieve(MaterialRequest("Revenue", [URL]))
    assert len(calls) == 1
    calls.clear()
    result = retrieve(MaterialRequest("Revenue", [URL], follow_links=1, link_domains=['example.gov.cn']))
    assert len(calls) == 2 and len(result.materials) == 1
    assert result.diagnostics[-1].code == 'blocked_url'
