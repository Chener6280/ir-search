from __future__ import annotations

from typing import Optional

from .models import FallbackPolicy, Intent, Query, TimeWindow


TOOL_NAMES = ["search"]


def list_tool_names() -> list[str]:
    return TOOL_NAMES[:]


def build_query(
    query: str,
    sources: Optional[list[str]] = None,
    count: int = 10,
    freshness: str = "noLimit",
    allow_browser_fallback: bool = False,
    intent: Optional[str] = None,
    fallback_policy: str = "none",
    fallback_on_empty: bool = False,
) -> Query:
    policy = FallbackPolicy(fallback_policy.lower())
    q = Query(
        text=query,
        sources=sources,
        count=count,
        window=TimeWindow(raw=freshness),
        allow_browser_fallback=allow_browser_fallback,
        allow_fallback=policy != FallbackPolicy.NONE,
        fallback_policy=policy,
        fallback_on_empty=fallback_on_empty,
    )
    if intent:
        q.intent = Intent(intent.lower())
    return q


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
        intent: Optional[str] = None,
        fallback_policy: str = "none",
        fallback_on_empty: bool = False,
    ) -> dict:
        q = build_query(
            query=query,
            sources=sources,
            count=count,
            freshness=freshness,
            allow_browser_fallback=allow_browser_fallback,
            intent=intent,
            fallback_policy=fallback_policy,
            fallback_on_empty=fallback_on_empty,
        )
        return ir_search(q).to_dict()

    mcp.run()


if __name__ == "__main__":
    run()
