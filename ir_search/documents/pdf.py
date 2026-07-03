from __future__ import annotations

from typing import Optional

from ir_search.models import EvidenceType, SourceTier

from .models import Document, hash_bytes, hash_text, make_doc_id, utc_now


def extract_pdf_document(
    raw: bytes,
    url: str,
    *,
    source_hint: Optional[str] = None,
    max_chars: int = 20000,
) -> Document:
    """Extract PDF text with PyMuPDF when the optional dependency is installed."""

    warnings: list[str] = []
    errors: list[str] = []
    page_texts: list[str] = []
    try:
        import fitz  # type: ignore

        with fitz.open(stream=raw, filetype="pdf") as pdf:
            for idx, page in enumerate(pdf, start=1):
                page_text = page.get_text("text").strip()
                if page_text:
                    page_texts.append(f"Page {idx}\n{page_text}")
    except ImportError:
        errors.append("PyMuPDF is not installed; install ir-search[extract] for PDF extraction")
    except Exception as exc:
        errors.append(f"pdf extraction failed: {exc}")

    text = "\n\n".join(page_texts)
    if len(text) > max_chars:
        text = text[:max_chars].rstrip()
        warnings.append(f"text truncated to max_chars={max_chars}")
    if not text:
        warnings.append("no PDF text extracted")
    raw_hash = hash_bytes(raw)
    text_hash = hash_text(text)
    return Document(
        doc_id=make_doc_id(url, text_hash or raw_hash),
        url=url,
        canonical_url=url,
        title=url.rsplit("/", 1)[-1] or url,
        source=source_hint or "web",
        source_tier=SourceTier.MEDIA,
        evidence_type=EvidenceType.ANNOUNCEMENT,
        content_type="pdf",
        published_at=None,
        fetched_at=utc_now(),
        extraction_method="pymupdf" if text else "pymupdf_unavailable_or_failed",
        text=text,
        raw_hash=raw_hash,
        text_hash=text_hash,
        warnings=warnings,
        errors=errors,
        extra={"source_text_trust": "untrusted"},
    )
