from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from ir_search.models import EvidenceType, SourceTier


@dataclass
class Document:
    """Fetched and normalized source document text."""

    doc_id: str
    url: str
    canonical_url: str
    title: str
    source: str
    source_tier: SourceTier
    evidence_type: EvidenceType
    content_type: str
    published_at: Optional[datetime]
    fetched_at: datetime
    extraction_method: str
    text: str
    tables: list[dict[str, Any]] = field(default_factory=list)
    links: list[dict[str, Any]] = field(default_factory=list)
    raw_hash: Optional[str] = None
    text_hash: Optional[str] = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return document_to_dict(self)


def make_doc_id(url: str, text_or_bytes_hash: str | bytes) -> str:
    """Build a stable, non-sensitive document id from URL and content hash."""

    if isinstance(text_or_bytes_hash, bytes):
        content_hash = hashlib.sha1(text_or_bytes_hash).hexdigest()
    else:
        content_hash = str(text_or_bytes_hash)
    digest = hashlib.sha1(f"{url}|{content_hash}".encode("utf-8")).hexdigest()[:20]
    return f"doc_{digest}"


def hash_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def hash_bytes(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def document_to_dict(document: Document) -> dict[str, Any]:
    data = asdict(document)
    data["source_tier"] = document.source_tier.name
    data["evidence_type"] = document.evidence_type.value
    data["published_at"] = document.published_at.isoformat() if document.published_at else None
    data["fetched_at"] = document.fetched_at.isoformat()
    return data


def document_from_dict(data: dict[str, Any]) -> Document:
    copied = dict(data)
    if copied.get("published_at"):
        copied["published_at"] = datetime.fromisoformat(copied["published_at"])
    if copied.get("fetched_at"):
        copied["fetched_at"] = datetime.fromisoformat(copied["fetched_at"])
    else:
        copied["fetched_at"] = utc_now()
    copied["source_tier"] = SourceTier[copied["source_tier"]]
    copied["evidence_type"] = EvidenceType(copied["evidence_type"])
    return Document(**copied)
