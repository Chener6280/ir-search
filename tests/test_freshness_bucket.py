from __future__ import annotations

from datetime import datetime, timezone

from ir_search.documents.models import Document, make_doc_id, utc_now
from ir_search.evidence import extract_evidence, freshness_bucket
from ir_search.evidence.models import ClaimVerification
from ir_search.models import EvidenceType, SourceTier
from ir_search.research.orchestrator import apply_freshness_requirements


NOW = datetime(2026, 7, 2, tzinfo=timezone.utc)


def test_freshness_bucket_values():
    assert freshness_bucket(datetime(2026, 6, 15, tzinfo=timezone.utc), now=NOW) == "recent_30d"
    assert freshness_bucket(datetime(2026, 4, 20, tzinfo=timezone.utc), now=NOW) == "recent_90d"
    assert freshness_bucket(datetime(2025, 12, 31, tzinfo=timezone.utc), now=NOW) == "historical"
    assert freshness_bucket(None, now=NOW) == "missing_date"


def test_extract_evidence_records_missing_date_bucket():
    doc = _doc(published_at=None)

    span = extract_evidence(doc, "海外 AI 光模块需求 收入", max_spans=1)[0]

    assert span.extra["freshness_bucket"] == "missing_date"


def test_recent_question_requires_recent_evidence_for_supported_claims():
    span = extract_evidence(_doc(published_at=datetime(2025, 1, 1, tzinfo=timezone.utc)), "海外 AI 光模块需求 收入", max_spans=1)[0]
    claim = ClaimVerification("c1", "海外 AI 光模块需求收入增长", "supported", 0.8, supporting_spans=[span])

    apply_freshness_requirements("最近海外 AI 光模块需求如何", [claim])

    assert claim.status == "mixed"
    assert any("historical/missing_date" in caveat for caveat in claim.caveats)


def test_recent_evidence_keeps_supported_status():
    span = extract_evidence(_doc(published_at=datetime.now(timezone.utc)), "海外 AI 光模块需求 收入", max_spans=1)[0]
    claim = ClaimVerification("c1", "海外 AI 光模块需求收入增长", "supported", 0.8, supporting_spans=[span])

    apply_freshness_requirements("最近海外 AI 光模块需求如何", [claim])

    assert claim.status == "supported"


def _doc(*, published_at):
    return Document(
        doc_id=make_doc_id("https://example.com/report", "hash"),
        url="https://example.com/report",
        canonical_url="https://example.com/report",
        title="AI 光模块需求",
        source="company_ir",
        source_tier=SourceTier.COMPANY,
        evidence_type=EvidenceType.ANNOUNCEMENT,
        content_type="html",
        published_at=published_at,
        fetched_at=utc_now(),
        extraction_method="fixture",
        text="海外 AI 光模块需求保持增长，收入和订单改善。",
    )
