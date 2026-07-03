from __future__ import annotations

import os

import pytest

from ir_search.research.orchestrator import deep_research
from ir_search.source_health import source_health


@pytest.mark.live
@pytest.mark.skipif(os.getenv("IR_SEARCH_LIVE") != "1", reason="IR_SEARCH_LIVE=1 required")
def test_cninfo_company_ir_live_smoke_returns_structured_diagnostics():
    health = source_health()
    sources = health.get("sources", {})
    official = {name: sources.get(name, {}) for name in ["cninfo", "company_ir"]}
    if not any(status.get("ok") and status.get("adapter_mode") == "live" for status in official.values()):
        pytest.skip(f"cninfo/company_ir live source unavailable: {official}")

    run = deep_research(
        "中际旭创 最新季报 官方公告",
        intent="earnings",
        max_searches=2,
        max_documents=2,
        source_health_fn=lambda: health,
    )
    payload = run.to_dict()

    assert payload["run_id"].startswith("dr_")
    assert "official_gap_report" in payload
    assert "actual_evidence_by_source" in payload
    for source in ["cninfo", "company_ir"]:
        row = payload["official_gap_report"]["actual_retrieval"].get(source, {})
        assert {"searched", "fetched_documents", "evidence_spans"} <= set(row)
