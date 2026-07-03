from __future__ import annotations

from ir_search.documents.models import Document, make_doc_id, utc_now
from ir_search.evidence.models import ClaimVerification, EvidenceSpan
from ir_search.models import EvidenceType, SourceTier
from ir_search.research.orchestrator import (
    build_actual_evidence_by_source,
    build_official_gap_report,
    build_official_source_attempts,
    build_source_capabilities,
)


def test_source_health_live_is_not_evidence():
    capabilities = build_source_capabilities(["cninfo"], {"sources": {"cninfo": {"ok": True, "adapter_mode": "live"}}})
    report = build_official_gap_report("公司最新季报", ["cninfo"], capabilities, {}, [])

    assert capabilities["cninfo"]["adapter_mode"] == "live"
    assert report["verdict"] == "insufficient_primary_source_evidence"
    assert report["actual_retrieval"]["cninfo"]["official_attempted"] is False
    assert report["actual_retrieval"]["cninfo"]["fetched_documents"] == 0
    assert report["actual_retrieval"]["cninfo"]["reason"] == "not_attempted"


def test_actual_evidence_by_source_counts_fetched_documents():
    doc = _document()
    span = _span(doc)
    claim = ClaimVerification("c1", "公司收入增长", "supported", 0.8, supporting_spans=[span])

    matrix = build_actual_evidence_by_source(
        [{"sources": ["cninfo"], "hit_sources": ["cninfo"]}],
        [doc],
        [span],
        [claim],
    )

    assert matrix["cninfo"]["searched"] is True
    assert matrix["cninfo"]["fetched_documents"] == 1
    assert matrix["cninfo"]["evidence_spans"] == 1
    assert matrix["cninfo"]["supporting_claims"] == ["公司收入增长"]


def test_official_gap_report_when_no_official_evidence():
    capabilities = {"cninfo": {"adapter_mode": "live", "ok": True}}
    actual = {"cninfo": {"searched": True, "fetched_documents": 0, "evidence_spans": 0, "supporting_claims": []}}

    report = build_official_gap_report("公司最新季报", ["cninfo"], capabilities, actual, [])

    assert report["verdict"] == "insufficient_primary_source_evidence"
    assert "cninfo announcements" in report["manual_checklist"]
    for field in [
        "required_for_claims",
        "official_sources_required",
        "source_capability",
        "actual_retrieval",
        "official_attempted",
        "official_sources_with_evidence",
        "official_supported_claims",
        "verdict",
        "manual_checklist",
    ]:
        assert field in report


def test_official_attempts_show_not_found_after_live_search():
    capabilities = {"cninfo": {"adapter_mode": "live", "ok": True}}
    actual = {"cninfo": {"searched": True, "fetched_documents": 0, "evidence_spans": 0, "supporting_claims": []}}

    attempts = build_official_source_attempts(["cninfo"], capabilities, actual)
    report = build_official_gap_report("最近是否有公开证据", ["cninfo"], capabilities, actual, [])

    assert attempts[0]["official_attempted"] is True
    assert attempts[0]["reason"] == "not_found"
    assert report["actual_retrieval"]["cninfo"]["official_attempted"] is True
    assert report["actual_retrieval"]["cninfo"]["reason"] == "not_found"


def test_placeholder_sources_listed_in_official_gap_report():
    capabilities = build_source_capabilities(["sse"], {"sources": {"sse": {"ok": False, "adapter_mode": "placeholder"}}})
    attempts = build_official_source_attempts(["sse"], capabilities, {})

    assert attempts[0]["capability"] == "placeholder"
    assert attempts[0]["status"] == "source_unavailable_or_placeholder"


def test_official_confirmed_requires_fetched_official_document():
    doc = _document()
    span = _span(doc)
    claim = ClaimVerification("c1", "公司收入增长", "supported", 0.8, supporting_spans=[span])
    actual = {"cninfo": {"searched": True, "fetched_documents": 1, "evidence_spans": 1, "supporting_claims": [claim.claim]}}

    report = build_official_gap_report("公司最新季报", ["cninfo"], {"cninfo": {"adapter_mode": "live", "ok": True}}, actual, [claim])

    assert report["verdict"] == "primary_source_evidence_present"


def test_official_gap_report_required_for_claims():
    claim = ClaimVerification("c1", "官方公告确认新增订单", "mixed", 0.5, supporting_spans=[])

    report = build_official_gap_report("公司最新订单", ["cninfo"], {"cninfo": {"adapter_mode": "live", "ok": True}}, {}, [claim])

    assert "官方公告确认新增订单" in report["required_for_claims"]


def test_official_gap_report_required_for_claims_for_official_question():
    report = build_official_gap_report(
        "该需求是否被官方一手证据确认",
        ["cninfo"],
        {"cninfo": {"adapter_mode": "live", "ok": True}},
        {},
        [],
    )

    assert report["required_for_claims"] == ["该需求是否被官方一手证据确认"]


def _document() -> Document:
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
    )


def _span(document: Document) -> EvidenceSpan:
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
        published_at=document.published_at,
        extra={"freshness_bucket": "missing_date", "content_type": "pdf"},
    )
