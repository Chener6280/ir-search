from __future__ import annotations

from ir_search.mcp_server import list_tool_names


def test_mcp_tool_list_contains_deep_research_surface():
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
