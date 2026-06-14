from ir_search.mcp_server import build_query, list_tool_names
from ir_search.models import FallbackPolicy, Intent


def test_mcp_exposes_only_search_tool():
    assert list_tool_names() == ["search"]


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
