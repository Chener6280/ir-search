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
    assert run.extra["official_second_pass"]["reason"] == "primary_sources_missing"
    assert run.extra["official_second_pass"]["diagnostics"]
    assert any(item["official_only"] for item in run.search_log)


def test_second_pass_records_placeholder_sources():
    run = deep_research(
        "公司最新季报是否验证收入增长",
        intent="earnings",
        max_searches=1,
        search_fn=lambda q: SearchResult(query=q, hits=[], diagnostics=[]),
        source_health_fn=lambda: {"sources": {"cninfo": {"ok": False, "adapter_mode": "placeholder"}}},
    )

    placeholders = run.extra["official_second_pass"].get("placeholder_sources") or []

    assert run.extra["official_second_pass"]["triggered"] is False
    assert any(item["source"] == "cninfo" and item["reason"] == "adapter_not_live" for item in placeholders)
    assert "official_second_pass" in run.answer


def test_second_pass_required_when_question_mentions_official_evidence():
    run = deep_research(
        "媒体信号是否能被官方一手证据确认",
        intent="auto",
        max_searches=2,
        search_fn=lambda q: SearchResult(query=q, hits=[], diagnostics=[SourceStatus("cninfo", False, 0, "mock", 1)]),
        source_health_fn=lambda: {"sources": {"cninfo": {"ok": False, "adapter_mode": "placeholder"}}},
    )

    second_pass = run.extra["official_second_pass"]

    assert second_pass["reason"] != "no_required_official_sources"
    assert "cninfo" in second_pass["required_sources"]
    assert run.extra["official_gap_report"]["official_sources_required"]


def test_second_pass_records_not_attempted_sources_when_budget_exhausted():
    run = deep_research(
        "媒体信号是否能被官方一手证据确认",
        intent="auto",
        max_searches=1,
        search_fn=lambda q: SearchResult(query=q, hits=[], diagnostics=[]),
        source_health_fn=lambda: {"sources": {"cninfo": {"ok": False, "adapter_mode": "placeholder"}}},
    )

    second_pass = run.extra["official_second_pass"]

    assert second_pass["triggered"] is False
    assert second_pass["reason"] == "search_budget_exhausted"
    assert second_pass["not_attempted_sources"]


def test_current_overseas_ai_optical_demand_runs_official_entity_queries():
    observed = []

    def search_fn(q: Query) -> SearchResult:
        observed.append(q)
        if q.sources:
            return SearchResult(
                query=q,
                hits=[],
                diagnostics=[SourceStatus(source, True, 0, None, 1, adapter_mode="live") for source in q.sources],
            )
        hit = Hit(
            title="媒体报道 AI 光模块需求",
            url="https://example.com/ai-optical-demand",
            snippet="媒体报道 AI 光模块海外需求增长。",
            source="bocha",
            tier=SourceTier.MEDIA,
            extra={"adapter_mode": "mock"},
        )
        return SearchResult(query=q, hits=[hit], diagnostics=[SourceStatus("bocha", True, 1, None, 1)])

    run = deep_research(
        "最近90天 AI 光模块 海外需求 是否有公开证据",
        intent="auto",
        max_searches=8,
        max_documents=4,
        search_fn=search_fn,
        source_health_fn=lambda: {
            "sources": {
                "cninfo": {"ok": True, "adapter_mode": "live"},
                "company_ir": {"ok": False, "adapter_mode": "mock"},
                "sec": {"ok": False, "adapter_mode": "placeholder"},
            }
        },
    )

    official_queries = [q.text for q in observed if q.sources]
    assert any("Coherent AI optical demand earnings call" == query for query in official_queries)
    assert any("Lumentum datacom AI demand backlog" == query for query in official_queries)
    assert any("Fabrinet optical communications AI datacenter revenue" == query for query in official_queries)
    assert any("Microsoft Meta Google Amazon capex networking AI optical" == query for query in official_queries)
    assert any(q.sources and "cninfo" in q.sources for q in observed)
    assert any(q.sources and q.window.raw == "90d" for q in observed)

    cninfo_attempt = next(item for item in run.extra["official_source_attempts"] if item["source"] == "cninfo")
    assert cninfo_attempt["official_attempted"] is True
    assert cninfo_attempt["fetched_documents"] == 0
    assert cninfo_attempt["reason"] == "not_found"
    assert run.extra["official_gap_report"]["actual_retrieval"]["cninfo"]["official_attempted"] is True
