from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Optional

from ir_search.models import EvidenceType, SourceTier


@dataclass
class EvidenceSpan:
    """A source-grounded text span that can be cited and re-ranked."""

    span_id: str
    doc_id: str
    url: str
    title: str
    source: str
    source_tier: SourceTier
    evidence_type: EvidenceType
    text: str
    relevance_score: float
    page: Optional[int] = None
    section: Optional[str] = None
    start_char: Optional[int] = None
    end_char: Optional[int] = None
    published_at: Optional[datetime] = None
    extracted_for_question: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["source_tier"] = self.source_tier.name
        data["evidence_type"] = self.evidence_type.value
        data["published_at"] = self.published_at.isoformat() if self.published_at else None
        return data


@dataclass
class ClaimVerification:
    """Deterministic evidence ledger entry for one claim."""

    claim_id: str
    claim: str
    status: str
    confidence: float
    supporting_spans: list[EvidenceSpan] = field(default_factory=list)
    contradicting_spans: list[EvidenceSpan] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    verification_queries: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "claim": self.claim,
            "status": self.status,
            "confidence": self.confidence,
            "supporting_spans": [span.to_dict() for span in self.supporting_spans],
            "contradicting_spans": [span.to_dict() for span in self.contradicting_spans],
            "caveats": list(self.caveats),
            "verification_queries": list(self.verification_queries),
        }
