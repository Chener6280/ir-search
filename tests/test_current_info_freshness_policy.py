from __future__ import annotations

from datetime import datetime, timezone

from ir_search.evidence.models import ClaimVerification, EvidenceSpan
from ir_search.models import EvidenceType, SourceTier
from ir_search.research.orchestrator import apply_freshness_requirements


def test_current_info_historical_or_missing_date_cannot_stay_supported():
    historical = EvidenceSpan(
        span_id="sp_old",
        doc_id="doc_old",
        url="https://example.com/old",
        title="历史报道",
        source="web",
        source_tier=SourceTier.MEDIA,
        evidence_type=EvidenceType.NEWS,
        text="公司需求增长。",
        relevance_score=0.6,
        published_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        extra={"freshness_bucket": "historical"},
    )
    missing = EvidenceSpan(
        span_id="sp_missing",
        doc_id="doc_missing",
        url="https://example.com/missing",
        title="无日期报道",
        source="web",
        source_tier=SourceTier.MEDIA,
        evidence_type=EvidenceType.NEWS,
        text="公司需求增长。",
        relevance_score=0.6,
        extra={"freshness_bucket": "missing_date"},
    )
    claim = ClaimVerification("c1", "最近公司需求增长", "supported", 0.8, supporting_spans=[historical, missing])

    apply_freshness_requirements("最近公司需求如何", [claim])

    assert claim.status == "insufficient_evidence"
    assert any("background only" in caveat for caveat in claim.caveats)
