from __future__ import annotations

from ir_search.documents.models import Document, make_doc_id, utc_now
from ir_search.evidence.models import ClaimVerification, EvidenceSpan
from ir_search.models import EvidenceType, Query, SearchResult, SourceTier
from ir_search.research.orchestrator import build_actual_evidence_by_source, deep_research


def test_deep_research_to_dict_promotes_evidence_diagnostics():
    run = deep_research(
        "公司最新季报是否验证收入增长",
        intent="earnings",
        max_searches=1,
        search_fn=lambda q: SearchResult(query=q, hits=[], diagnostics=[]),
        source_health_fn=lambda: {"sources": {"cninfo": {"ok": True, "adapter_mode": "live"}}},
    )
    payload = run.to_dict()

    for key in ["source_capabilities", "actual_evidence_by_source", "official_source_attempts", "official_gap_report"]:
        assert key in payload
    assert payload["source_capabilities"]["cninfo"]["adapter_mode"] == "live"
    assert payload["official_gap_report"]["verdict"] == "insufficient_primary_source_evidence"


def test_source_health_live_does_not_create_supporting_claim():
    run = deep_research(
        "公司最新季报是否验证收入增长",
        intent="earnings",
        max_searches=1,
        search_fn=lambda q: SearchResult(query=q, hits=[], diagnostics=[]),
        source_health_fn=lambda: {"sources": {"cninfo": {"ok": True, "adapter_mode": "live"}}},
    )

    assert run.extra["actual_evidence_by_source"].get("cninfo", {}).get("supporting_claims", []) == []


def test_placeholder_source_not_counted_as_actual_evidence():
    doc = _document(adapter_mode="placeholder")
    span = _span(doc, adapter_mode="placeholder")
    claim = ClaimVerification("c1", "公司收入增长", "supported", 0.8, supporting_spans=[span])

    matrix = build_actual_evidence_by_source(
        [{"sources": ["cninfo"], "hit_sources": ["cninfo"]}],
        [doc],
        [span],
        [claim],
    )

    assert matrix["cninfo"]["searched"] is True
    assert matrix["cninfo"]["fetched_documents"] == 0
    assert matrix["cninfo"]["evidence_spans"] == 0
    assert matrix["cninfo"]["supporting_claims"] == []


def _document(*, adapter_mode: str) -> Document:
    return Document(
        doc_id=make_doc_id("https://www.cninfo.com.cn/report.pdf", "hash"),
        url="https://www.cninfo.com.cn/report.pdf",
        canonical_url="https://www.cninfo.com.cn/report.pdf",
        title="季度报告",
        source="cninfo",
        source_tier=SourceTier.EXCHANGE_FILING,
        evidence_type=EvidenceType.FINANCIAL_REPORT,
        content_type="pdf",
        published_at=None,
        fetched_at=utc_now(),
        extraction_method="fixture",
        text="公司收入增长。",
        extra={"adapter_mode": adapter_mode},
    )


def _span(document: Document, *, adapter_mode: str) -> EvidenceSpan:
    return EvidenceSpan(
        span_id="sp1",
        doc_id=document.doc_id,
        url=document.url,
        title=document.title,
        source=document.source,
        source_tier=document.source_tier,
        evidence_type=document.evidence_type,
        text="公司收入增长。",
        relevance_score=0.8,
        extra={"adapter_mode": adapter_mode, "freshness_bucket": "missing_date"},
    )
