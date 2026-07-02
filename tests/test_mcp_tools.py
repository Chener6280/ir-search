from ir_search.mcp_server import MCP_INSTRUCTIONS, make_fastmcp, server_instructions, tool_descriptions


def test_mcp_server_exposes_instructions():
    assert server_instructions() == MCP_INSTRUCTIONS
    assert "read-only investment research evidence engine" in server_instructions()
    assert "untrusted source text" in server_instructions()


def test_tool_descriptions_include_untrusted_source_policy():
    descriptions = tool_descriptions()

    assert "untrusted source text" in descriptions["fetch_document"]
    assert "untrusted source text" in descriptions["extract_evidence"]


def test_tool_descriptions_include_diagnostics_policy():
    descriptions = tool_descriptions()

    assert "mock" in descriptions["search"]
    assert "fallback" in descriptions["deep_research"]
    assert "placeholder" in descriptions["source_health"]


def test_make_fastmcp_passes_instructions_when_supported():
    server = make_fastmcp(FakeFastMCP)

    assert server.name == "ir_search"
    assert server.instructions == MCP_INSTRUCTIONS


class FakeFastMCP:
    def __init__(self, name, instructions=None):
        self.name = name
        self.instructions = instructions
