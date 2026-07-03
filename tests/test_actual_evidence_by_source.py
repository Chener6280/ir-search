from __future__ import annotations

from ir_search.documents.models import Document, make_doc_id, utc_now
from ir_search.evidence.models import ClaimVerification, EvidenceSpan
from ir_search.models import EvidenceType, SourceTier
from ir_search.research.orchestrator import build_actual_evidence_by_source


def test_searched_source_with_no_documents_is_visible():
    matrix = build_actual_evidence_by_source(
        [{"sources": ["cninfo"], "hit_sources": []}],
        [],
        [],
        [],
    )

    assert matrix["cninfo"]["searched"] is True
    assert matrix["cninfo"]["fetched_documents"] == 0
    assert matrix["cninfo"]["evidence_spans"] == 0


def test_fetched_official_doc_with_span_counts_as_actual_evidence():
    doc = _document()
    span = _span(doc)
    claim = ClaimVerification("c1", "公司收入增长", "supported", 0.8, supporting_spans=[span])

    matrix = build_actual_evidence_by_source(
        [{"sources": ["cninfo"], "hit_sources": ["cninfo"]}],
        [doc],
        [span],
        [claim],
    )

    assert matrix["cninfo"]["fetched_documents"] == 1
    assert matrix["cninfo"]["evidence_spans"] == 1
    assert matrix["cninfo"]["supporting_claims"] == ["公司收入增长"]


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
        extra={"freshness_bucket": "missing_date"},
    )
