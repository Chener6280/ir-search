from __future__ import annotations

import json
from pathlib import Path

from ir_search.models import EvidenceType, Hit, Query, SearchResult, SourceStatus, SourceTier
from ir_search.research.orchestrator import deep_research


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "deep_research_missing_date.json"


def test_deep_research_missing_date_span_gets_bucket():
    run = _run_missing_date_fixture()

    assert run.evidence_spans
    assert all(span.extra["freshness_bucket"] == "missing_date" for span in run.evidence_spans)


def test_missing_date_does_not_support_current_claim():
    run = _run_missing_date_fixture()

    assert run.claim_ledger
    assert all(entry.status != "supported" for entry in run.claim_ledger if entry.supporting_spans)


def test_answer_discloses_missing_date_caveat():
    run = _run_missing_date_fixture()

    assert "missing_date" in run.answer
    assert "historical/missing_date evidence is background only" in run.answer


def _run_missing_date_fixture():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    hit_data = fixture["hit"]

    def search_fn(q: Query) -> SearchResult:
        hit = Hit(
            title=hit_data["title"],
            url=hit_data["url"],
            snippet=hit_data["snippet"],
            source=hit_data["source"],
            tier=SourceTier[hit_data["source_tier"]],
            evidence_type=EvidenceType(hit_data["evidence_type"]),
            published_at=None,
            extra={"adapter_mode": "mock"},
        )
        return SearchResult(query=q, hits=[hit], diagnostics=[SourceStatus("bocha", True, 1, None, 1, adapter_mode="mock")])

    return deep_research(
        fixture["question"],
        intent="industry_chain",
        max_searches=1,
        max_documents=1,
        search_fn=search_fn,
        source_health_fn=lambda: {"sources": {}},
    )
