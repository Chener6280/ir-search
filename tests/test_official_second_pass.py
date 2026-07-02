from __future__ import annotations

from ir_search.models import Hit, Query, SearchResult, SourceStatus, SourceTier
from ir_search.research.orchestrator import deep_research


def test_official_only_second_pass_triggers_when_first_pass_has_only_media():
    observed_sources = []

    def search_fn(q: Query) -> SearchResult:
        observed_sources.append(q.sources)
        if q.sources:
            hit = Hit(
                title="公司公告",
                url="https://www.cninfo.com.cn/new/disclosure/detail",
                snippet="公司公告显示收入增长。",
                source="cninfo",
                tier=SourceTier.EXCHANGE_FILING,
                extra={"adapter_mode": "mock"},
            )
            return SearchResult(query=q, hits=[hit], diagnostics=[SourceStatus("cninfo", True, 1, None, 1)])
        hit = Hit(
            title="媒体报道",
            url="https://www.stcn.com/article/detail/1.html",
            snippet="媒体报道公司收入增长。",
            source="bocha",
            tier=SourceTier.MEDIA,
        )
        return SearchResult(query=q, hits=[hit], diagnostics=[SourceStatus("bocha", True, 1, None, 1)])

    run = deep_research(
        "公司最新季报是否验证收入增长",
        intent="earnings",
        max_searches=3,
        max_documents=4,
        search_fn=search_fn,
        source_health_fn=lambda: {"sources": {"cninfo": {"ok": True, "adapter_mode": "live"}}},
    )

    assert any(sources for sources in observed_sources)
    assert run.extra["official_second_pass"]["triggered"] is True
    assert any(item["official_only"] for item in run.search_log)
