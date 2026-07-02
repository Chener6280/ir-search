from __future__ import annotations

import urllib.error
import urllib.request
from typing import Optional

from ir_search.models import EvidenceType, Hit, SourceTier

from .html import extract_html_document
from .models import Document, hash_bytes, hash_text, make_doc_id, utc_now
from .pdf import extract_pdf_document
from .safety import ensure_url_allowed
from .wechat import document_from_wechat_hit, is_wechat_url


def fetch_document(
    url: str,
    *,
    source_hint: Optional[str] = None,
    max_chars: int = 20000,
    include_tables: bool = True,
    allow_private_network: bool = False,
    timeout_sec: int = 20,
) -> Document:
    """Fetch a URL and return normalized source text with warnings/errors."""

    del include_tables
    ensure_url_allowed(url, allow_private_network=allow_private_network)
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/pdf,text/plain,*/*;q=0.8",
            "User-Agent": "Mozilla/5.0 ir_search/0.1",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read(5_000_000)
            content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    except urllib.error.HTTPError as exc:
        return _error_document(url, source_hint, f"http error {exc.code}: {exc.reason}", content_type="unknown")
    except Exception as exc:
        return _error_document(url, source_hint, f"fetch failed: {exc}", content_type="unknown")

    if content_type == "application/pdf" or url.lower().split("?", 1)[0].endswith(".pdf"):
        return extract_pdf_document(raw, url, source_hint=source_hint, max_chars=max_chars)
    if content_type.startswith("text/plain") or content_type in {"text/markdown", "application/json"}:
        return _text_document(raw, url, source_hint=source_hint, content_type=content_type or "text", max_chars=max_chars)
    document = extract_html_document(raw, url, source_hint="wechat" if is_wechat_url(url, source_hint) else source_hint, max_chars=max_chars)
    if source_hint and document.source == "web":
        document.source = source_hint
    return document


def document_from_hit(hit: Hit, *, max_chars: int = 20000, fetch_errors: Optional[list[str]] = None) -> Document:
    """Build a Document from a search hit when full fetching is unavailable or unnecessary."""

    if hit.source == "manual_wechat" or hit.extra.get("content"):
        return document_from_wechat_hit(hit, max_chars=max_chars)
    text = hit.snippet or hit.title
    warnings = ["document built from search hit snippet; fetch full document for stronger evidence"]
    if hit.extra.get("adapter_mode") == "mock":
        warnings.append("source hit came from mock adapter")
    if fetch_errors:
        warnings.extend(fetch_errors)
    if len(text) > max_chars:
        text = text[:max_chars].rstrip()
    text_hash = hash_text(text)
    return Document(
        doc_id=make_doc_id(hit.canonical_url or hit.url, text_hash),
        url=hit.url,
        canonical_url=hit.canonical_url or hit.url,
        title=hit.title,
        source=hit.source,
        source_tier=hit.tier or SourceTier.MEDIA,
        evidence_type=hit.evidence_type if hit.evidence_type != EvidenceType.UNKNOWN else EvidenceType.UNKNOWN,
        content_type="snippet",
        published_at=hit.published_at,
        fetched_at=utc_now(),
        extraction_method="search_hit_snippet_fallback",
        text=text,
        raw_hash=None,
        text_hash=text_hash,
        warnings=warnings,
        errors=[],
        extra={
            "adapter_mode": hit.extra.get("adapter_mode"),
            "is_fallback_result": hit.extra.get("is_fallback_result", False),
            "source_text_trust": "untrusted",
        },
    )


def _text_document(
    raw: bytes,
    url: str,
    *,
    source_hint: Optional[str],
    content_type: str,
    max_chars: int,
) -> Document:
    text = raw.decode("utf-8", errors="replace")
    warnings: list[str] = []
    if len(text) > max_chars:
        text = text[:max_chars].rstrip()
        warnings.append(f"text truncated to max_chars={max_chars}")
    raw_hash = hash_bytes(raw)
    text_hash = hash_text(text)
    source = source_hint or "web"
    return Document(
        doc_id=make_doc_id(url, text_hash),
        url=url,
        canonical_url=url,
        title=url.rsplit("/", 1)[-1] or url,
        source=source,
        source_tier=SourceTier.MEDIA,
        evidence_type=EvidenceType.OPINION if source_hint == "wechat" else EvidenceType.NEWS,
        content_type="wechat" if source_hint == "wechat" else "text",
        published_at=None,
        fetched_at=utc_now(),
        extraction_method="plain_text",
        text=text,
        raw_hash=raw_hash,
        text_hash=text_hash,
        warnings=warnings,
        errors=[],
        extra={"source_text_trust": "untrusted"},
    )


def _error_document(url: str, source_hint: Optional[str], error: str, *, content_type: str) -> Document:
    text_hash = hash_text("")
    return Document(
        doc_id=make_doc_id(url, text_hash),
        url=url,
        canonical_url=url,
        title=url.rsplit("/", 1)[-1] or url,
        source=source_hint or "web",
        source_tier=SourceTier.MEDIA,
        evidence_type=EvidenceType.UNKNOWN,
        content_type=content_type,
        published_at=None,
        fetched_at=utc_now(),
        extraction_method="fetch_failed",
        text="",
        raw_hash=None,
        text_hash=text_hash,
        warnings=[],
        errors=[error],
        extra={"source_text_trust": "untrusted"},
    )
