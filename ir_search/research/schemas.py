from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from ir_search.documents.models import Document
from ir_search.evidence.models import ClaimVerification, EvidenceSpan


@dataclass
class ResearchRun:
    """Auditable result of a deterministic research workflow."""

    run_id: str
    question: str
    started_at: datetime
    finished_at: Optional[datetime]
    search_log: list[dict[str, Any]]
    documents_read: list[Document]
    evidence_spans: list[EvidenceSpan]
    claim_ledger: list[ClaimVerification]
    source_matrix: list[dict[str, Any]]
    answer: str
    diagnostics: list[dict[str, Any]]
    unverified_items: list[str]
    source_text_trust: str = "untrusted"
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "question": self.question,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "search_log": self.search_log,
            "documents_read": [document.to_dict() for document in self.documents_read],
            "evidence_spans": [span.to_dict() for span in self.evidence_spans],
            "claim_ledger": [entry.to_dict() for entry in self.claim_ledger],
            "source_matrix": self.source_matrix,
            "answer": self.answer,
            "diagnostics": self.diagnostics,
            "unverified_items": self.unverified_items,
            "source_text_trust": self.source_text_trust,
            "extra": self.extra,
        }
