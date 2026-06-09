from __future__ import annotations

from typing import Optional

from .models import Query, TimeWindow


TOOL_NAMES = ["search"]


def list_tool_names() -> list[str]:
    return TOOL_NAMES[:]


def run() -> None:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise SystemExit("Install MCP support with: python -m pip install 'ir-search[mcp]'") from exc

    from .kernel import search as ir_search

    mcp = FastMCP("ir_search")

    @mcp.tool()
    def search(
        query: str,
        sources: Optional[list[str]] = None,
        count: int = 10,
        freshness: str = "noLimit",
        allow_browser_fallback: bool = False,
    ) -> dict:
        q = Query(
            text=query,
            sources=sources,
            count=count,
            window=TimeWindow(raw=freshness),
            allow_browser_fallback=allow_browser_fallback,
        )
        return ir_search(q).to_dict()

    mcp.run()


if __name__ == "__main__":
    run()
