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
