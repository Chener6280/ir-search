"""Synthetic web discovery/origin records; no network or private fixtures."""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import json

import pytest

from ir_search import (MaterialRegistry, MaterialSearchRequest, RequestContext, search_materials,
                       build_material_registry, MaterialSourceScan)
from ir_search import mcp_server
from ir_search.adapters.web_materials import WebMaterialAdapter
from ir_search.documents.html import extract_html_document
from ir_search.infrastructure.credentials import WebMaterialProfile, web_material_profile, SourceConfigError, source_configuration_status
from ir_search.infrastructure.public_web import _Reply
from ir_search.infrastructure.web_search import AnySearchWebClient, WebSearchPage
from ir_search.registry import DataAdapterError

NOW = datetime(2026, 9, 15, tzinfo=timezone.utc)
PROFILE = WebMaterialProfile(allow_anonymous=True)


def row(host="www.example.gov.cn", index=1, **changes):
    return dict({"url": f"https://{host}/policy/{index}", "title": "促进智能家居消费行动方案通知",
                 "snippet": "智能家居消费政策的搜索摘要，原文尚未读取。"}, **changes)


def document(url, *, title="促进智能家居消费行动方案通知", published="2026-09-14T12:00:00+08:00", text="智能家居消费政策明确适用范围与实施要求。"):
    content = f'<title>{title}</title><meta name="pubdate" content="{published}"><p>{text}</p>'
    doc = extract_html_document(content.encode(), url, max_chars=20000)
    doc.extra["adapter_mode"] = "live"
    doc.fetched_at = NOW
    return doc


def request(**kw):
    options = dict(question="智能家居消费政策", keywords=["智能家居"], published_start="2026-09-01", published_end="2026-09-15",
                   providers=["web"], candidates_per_source=5, text_reads_per_source=2)
    return MaterialSearchRequest(**dict(options, **kw))


class Client:
    def __init__(self, rows=None): self.rows = rows if rows is not None else [row()]; self.calls = []
    def search(self, query, *, limit, context):
        context.begin_operation(); self.calls.append((query, limit)); return WebSearchPage(self.rows, NOW)


def setup(rows=None, reader=None, profile=PROFILE):
    client, reads = Client(rows), []
    def read(url, **kwargs):
        kwargs["context"].begin_operation(); reads.append(url)
        return reader(url, **kwargs) if reader else document(url)
    reg = MaterialRegistry(); reg.register(WebMaterialAdapter(profile, client=client, reader=read))
    return reg, client, reads


def test_public_origin_material_provenance_dates_and_character_citations():
    reg, client, reads = setup()
    result = search_materials(request(), registry=reg).to_dict()
    assert result["status"] == "partial" and not result["complete"] and len(reads) == 1
    version = result["items"][0]["versions"][0]
    assert version["material_type"] == "policy" and version["text_scope"] == "extracted_text"
    assert version["discovery_provider"] == "anysearch"
    assert version["provenance"]["provider"] == "web" and version["provenance"]["publisher"] == "www.example.gov.cn"
    assert version["provenance"]["authority"] == "regulator" and version["published_at"].endswith("+08:00")
    assert "origin_text_extracted_not_independently_verified" in version["warnings"]
    assert "搜索摘要" not in version["text"]
    for span in version["evidence_spans"]:
        assert span["text"] == version[span["source_part"]][span["start_char"]:span["end_char"]]
    scan = result["coverage"][0]["scans"][0]
    assert scan["date_filter_basis"] == "local_publication_metadata" and scan["received_count"] == scan["inspected_count"] == 1
    assert "2026-09-01" in client.calls[0][0] and "2026-09-15" in client.calls[0][0]


def test_snippet_scope_budgets_and_partial_fetch_failure_remain_explicit():
    def read(url, **kwargs):
        if url.endswith("/1"): raise DataAdapterError("entitlement_denied")
        return document(url)
    reg, _, reads = setup([row(index=i) for i in range(1, 5)], reader=read)
    result = search_materials(request(), registry=reg)
    assert len(reads) == 2
    versions = [v for g in result.items for v in g["versions"]]
    scopes = result.coverage[0]["returned_evidence"]["text_scope_counts"]
    assert scopes == {"metadata": 0, "abstract": 0, "search_snippet": 3, "extracted_text": 1, "source_excerpt": 0}
    snippets = [v for v in versions if v["text_scope"] == "search_snippet"]
    assert all(v["provenance"]["authority"] == "anonymous_search" for v in snippets)
    assert all("search_snippet_not_original_text" in v["warnings"] for v in snippets)
    assert all(s["text_scope"] in {"metadata", "search_snippet"} for v in snippets for s in v["evidence_spans"])
    assert "entitlement_denied" in {d.code for d in result.diagnostics}


def test_dates_use_origin_metadata_and_filter_out_of_window_but_retain_unknown():
    rows = [row(index=1, published_at="2026-09-10"), row(index=2), row(index=3)]
    def read(url, **kwargs):
        return document(url, published={"1":"2025-09-10", "2":"", "3":"2026-09-14 12:00:00"}[url[-1]])
    reg, _, _ = setup(rows, reader=read)
    result = search_materials(request(text_reads_per_source=3), registry=reg)
    versions = [v for g in result.items for v in g["versions"]]
    assert len(versions) == 2 and "web_outside_publication_window" in {d.code for d in result.diagnostics}
    assert any(v["published_on"] is None and "publication_date_unknown" in v["warnings"] for v in versions)
    assert any(v["published_on"] == "2026-09-14" and v["published_at"] is None for v in versions)


def test_unknown_types_are_web_pages_not_invented_news_and_requested_kinds_filter():
    reg, _, _ = setup([row(host="example.test")])
    result = search_materials(request(), registry=reg)
    assert result.items[0]["material_type"] == "web_page"
    assert result.items[0]["versions"][0]["provenance"]["evidence_type"] == "unknown"
    assert not search_materials(request(material_types=["policy"]), registry=reg).items
    reg, client, reads = setup()
    result = search_materials(request(material_types=["research_report"]), registry=reg)
    assert not client.calls and not reads and result.coverage[0]["state"] == "material_type_not_supported"


def test_exact_snippets_across_urls_are_not_merged_as_identical_documents():
    text = "智能家居搜索摘要" * 40
    reg, _, reads = setup([row(index=1, snippet=text), row(index=2, snippet=text)])
    result = search_materials(request(text_reads_per_source=0), registry=reg)
    assert len(result.items) == 2 and not reads
    reg, _, _ = setup([row(), row()])
    result = search_materials(request(), registry=reg)
    assert len(result.items) == 1 and "duplicate_web_result" in {d.code for d in result.diagnostics}


@pytest.mark.parametrize("bad_url", ["http://127.0.0.1/a", "https://a.test/?token=secret", "file:///tmp/private", "https://example.gov.cn.attacker.test/a"])
def test_restricted_domains_and_unsafe_urls_are_skipped_before_read(bad_url):
    profile = replace(PROFILE, allowed_domains=("gov.cn",))
    reg, client, reads = setup([row(url=bad_url)], profile=profile)
    result = search_materials(request(), registry=reg)
    assert not result.items and not reads and "blocked_url" in {d.code for d in result.diagnostics}
    assert "site:gov.cn" in client.calls[0][0]


def test_limits_metadata_invalid_records_empty_and_cancelled():
    reg, client, reads = setup([row(index=i, snippet="") for i in range(20)])
    result = search_materials(request(candidates_per_source=20, text_reads_per_source=0), registry=reg)
    assert client.calls[0][1] == 10 and not reads and len(result.items) == 10
    assert result.coverage[0]["scanned_count"] == 10 and result.coverage[0]["scans"][0]["received_count"] == 20
    assert all(v["text_scope"] == "metadata" for g in result.items for v in g["versions"])
    reg, _, reads = setup([row(title=None), row(index=2)])
    result = search_materials(request(), registry=reg)
    assert len(result.items) == 1 and len(reads) == 1 and "invalid_web_record" in {d.code for d in result.diagnostics}
    reg, client, reads = setup([])
    assert not search_materials(request(), registry=reg).items
    client.calls.clear(); context=RequestContext(); context.cancel()
    search_materials(request(), registry=reg, context=context)
    assert not client.calls and not reads


def test_profile_opt_in_isolated_config_and_safe_diagnostics(tmp_path):
    assert web_material_profile(values={"ANYSEARCH_API_KEY":"synthetic_secret"}) is None
    with pytest.raises(SourceConfigError): web_material_profile(values={"WEB_MATERIALS_ENABLED":"true"})
    for options in [dict(allow_anonymous=1), dict(api_key="https://auth.test/key"),dict(allowed_domains=("a.test/path",)),dict(allowed_domains="a.test")]:
        with pytest.raises(SourceConfigError): replace(PROFILE, **options)
    with pytest.raises(ValueError): WebMaterialAdapter(None)
    with pytest.raises(ValueError): AnySearchWebClient(None)
    with pytest.raises(ValueError): MaterialSourceScan("search","web_page","2026-09-01","2026-09-15","queried",date_filter_basis="guessed")
    path=tmp_path/"private.env"; path.write_text("WEB_MATERIALS_ENABLED=true\nWEB_ALLOW_ANONYMOUS=true\n"); path.chmod(0o600)
    assert [a.name for a in build_material_registry(env_file=path).entries()] == ["web"]
    status=next(s for s in source_configuration_status(env_file=path)["sources"] if s["provider"]=="web")
    assert status["authentication_mode"]=="anonymous" and not status["live_verified"]
    path.write_text("WEB_MATERIALS_ENABLED=true\nWEB_SEARCH_PROVIDER=unknown\nTUSHARE_CORPUS_ENABLED=true\nTUSHARE_MCP_TOKEN=synthetic_token\n")
    registry=build_material_registry(env_file=path)
    assert [a.name for a in registry.entries()]==["tushare_corpus"] and registry.diagnostics[0].provider=="web"


def test_anysearch_wire_contract_drops_generated_content_and_account_fields():
    calls=[]
    def transport(url, **kw):
        calls.append((url,kw))
        payload={"code":0,"data":{"results":[dict(row(), content="generated_answer", api_key="issued_secret")],"auto_registered":{"api_key":"issued_secret"}}}
        return _Reply(200,"application/json","",json.dumps(payload).encode(),NOW)
    client=AnySearchWebClient(WebMaterialProfile("synthetic_secret"),transport=transport)
    result=client.search("智能家居",limit=3,context=RequestContext())
    assert calls[0][0]=="https://api.anysearch.com/v1/search"
    assert json.loads(calls[0][1]["body"])=={"query":"智能家居","max_results":3}
    assert calls[0][1]["headers"]["Authorization"]=="Bearer synthetic_secret"
    assert "generated_answer" not in repr(result.rows) and "issued_secret" not in repr(result.rows)
    assert "synthetic_secret" not in repr(client._profile)
    for query,limit in [("",1),("x",True),("x",11)]:
        with pytest.raises(DataAdapterError): client.search(query,limit=limit,context=RequestContext())
    assert len(calls)==1


@pytest.mark.parametrize("payload,code", [({"code":1,"message":"quota exhausted secret"},"quota"),
    ({"code":1,"message":"rate limited secret"},"rate_limit"),({"code":0,"data":{}},"upstream_schema"),
    ({"code":False,"data":{"results":[]}},"upstream_schema"),({"code":0,"data":{"results":[1]}},"upstream_schema"),
    ({"code":0,"data":{"results":[row(snippet="synthetic_secret")]}},"upstream_schema")])
def test_api_failures_are_sanitized(payload,code):
    client=AnySearchWebClient(WebMaterialProfile("synthetic_secret"),transport=lambda *a,**kw:_Reply(200,"application/json","",json.dumps(payload).encode(),NOW))
    with pytest.raises(DataAdapterError,match=code) as caught: client.search("智能家居",limit=3,context=RequestContext())
    assert "secret" not in str(caught.value)


def test_real_fastmcp_web_output_matches_sdk(monkeypatch):
    runtime=pytest.importorskip("mcp.server.fastmcp")
    reg,_,_=setup(); expected=search_materials(request(),registry=reg).to_dict()
    server=runtime.FastMCP("web-offline"); monkeypatch.setattr(server,"run",lambda:None)
    monkeypatch.setattr(mcp_server,"make_fastmcp",lambda _:server)
    original=mcp_server.search_materials_payload
    monkeypatch.setattr(mcp_server,"search_materials_payload",lambda args,**kw:original(args,registry=reg,**kw))
    mcp_server.run()
    async def check():
        response=await server.call_tool("search_materials",request().to_dict())
        actual=json.loads(response[0].text)
        assert actual["items"]==expected["items"] and actual["coverage"]==expected["coverage"]
    asyncio.run(check())
