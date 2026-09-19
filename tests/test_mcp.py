from ir_search.mcp_server import build_query, fetch_document_payload, list_tool_names, source_health_payload
from ir_search.models import FallbackPolicy, Intent


def test_mcp_exposes_research_tools():
    assert list_tool_names() == [
        "search",
        "fetch_document",
        "extract_evidence",
        "verify_claims",
        "deep_research",
        "source_health",
        "list_capabilities",
        "describe_dataset",
        "get_data",
        "retrieve",
        "search_announcements",
        "search_materials",
    ]


def test_mcp_build_query_exposes_intent_and_fallback():
    q = build_query(
        query="中际旭创 研报",
        sources=["bocha"],
        intent="BROKER_RESEARCH",
        fallback_policy="QUOTA_ONLY",
        fallback_on_empty=True,
    )

    assert q.intent == Intent.BROKER_RESEARCH
    assert q.allow_fallback is True
    assert q.fallback_policy == FallbackPolicy.QUOTA_ONLY
    assert q.fallback_on_empty is True


def test_mcp_fetch_document_blocks_unsafe_url():
    payload = fetch_document_payload("file:///etc/passwd")

    assert payload["errors"]
    assert "blocked_by_policy" in payload["errors"][0]


def test_mcp_source_health_payload_has_no_secret_values(monkeypatch):
    monkeypatch.setenv("BOCHA_API_KEY", "secret")

    payload = source_health_payload()

    assert payload["env"]["has_BOCHA_API_KEY"] is True
    assert "secret" not in str(payload)


def test_legacy_health_failure_preserves_new_source_status_without_raw_error(monkeypatch):
    def fail():
        raise ImportError("legacy import must_not_escape")
    monkeypatch.setattr("ir_search.mcp_server.source_health_impl", fail)
    payload = source_health_payload()
    assert payload["status"] == "partial"
    assert payload["diagnostics"][0]["code"] == "legacy_health_unavailable"
    assert {item["provider"] for item in payload["configured_sources"]["sources"]} == {"wind_mysql", "jydb", "akshare", "fmp", "tushare_corpus", "web", "zsxq", "wechat", "ima", "wisburg", "xueqiu", "eastmoney", "video", "xiaoyuzhou", "sec", "fiona", "alphapai", "gangtise", "xhs", "rss", "global_macro", "hkex", "company_ir"}
    assert "must_not_escape" not in str(payload)
