from __future__ import annotations

from typing import Optional

from ir_search.models import EvidenceType, Hit, SourceTier

from .models import Document, hash_text, make_doc_id, utc_now


def document_from_wechat_hit(hit: Hit, *, max_chars: int = 20000) -> Document:
    """Build a Document from a locally stored WeChat hit."""

    text = str(hit.extra.get("content") or hit.snippet or "")
    warnings: list[str] = []
    if len(text) > max_chars:
        text = text[:max_chars].rstrip()
        warnings.append(f"text truncated to max_chars={max_chars}")
    if not text:
        warnings.append("wechat hit had no content; using an empty document")
    text_hash = hash_text(text)
    return Document(
        doc_id=make_doc_id(hit.canonical_url or hit.url, text_hash),
        url=hit.url,
        canonical_url=hit.canonical_url or hit.url,
        title=hit.title,
        source=hit.source,
        source_tier=hit.tier or SourceTier.MEDIA,
        evidence_type=hit.evidence_type if hit.evidence_type != EvidenceType.UNKNOWN else EvidenceType.OPINION,
        content_type="wechat",
        published_at=hit.published_at,
        fetched_at=utc_now(),
        extraction_method=str(hit.extra.get("extraction_method") or "wechat_hit_content"),
        text=text,
        raw_hash=None,
        text_hash=text_hash,
        warnings=warnings,
        errors=[],
        extra={
            "account_name": hit.extra.get("account_name"),
            "content_path": hit.extra.get("content_path"),
            "source_text_trust": "untrusted",
        },
    )


def is_wechat_url(url: str, source_hint: Optional[str] = None) -> bool:
    return source_hint == "wechat" or "mp.weixin.qq.com" in url
