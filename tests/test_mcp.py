from ir_search.mcp_server import list_tool_names


def test_mcp_exposes_only_search_tool():
    assert list_tool_names() == ["search"]
