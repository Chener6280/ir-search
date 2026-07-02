from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from ir_search.models import EvidenceType, Hit, SourceTier

from .html import extract_html_document
from .models import Document, hash_bytes, hash_text, make_doc_id, utc_now
from .pdf import extract_pdf_document
from .safety import UrlBlockedError, ensure_url_allowed
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
    try:
        raw, content_type, final_url, redirect_chain = _fetch_bytes_with_redirects(
            url,
            allow_private_network=allow_private_network,
            timeout_sec=timeout_sec,
        )
    except UrlBlockedError as exc:
        return _error_document(
            url,
            source_hint,
            f"blocked_by_policy: {exc}",
            content_type="unknown",
            redirect_chain=getattr(exc, "redirect_chain", []),
        )
    except RedirectLimitError as exc:
        return _error_document(
            url,
            source_hint,
            str(exc),
            content_type="unknown",
            redirect_chain=exc.redirect_chain,
        )
    except urllib.error.HTTPError as exc:
        return _error_document(url, source_hint, f"http error {exc.code}: {exc.reason}", content_type="unknown")
    except Exception as exc:
        return _error_document(url, source_hint, f"fetch failed: {exc}", content_type="unknown")

    if content_type == "application/pdf" or final_url.lower().split("?", 1)[0].endswith(".pdf"):
        document = extract_pdf_document(raw, final_url, source_hint=source_hint, max_chars=max_chars)
        _attach_redirect_metadata(document, requested_url=url, redirect_chain=redirect_chain)
        return document
    if content_type.startswith("text/plain") or content_type in {"text/markdown", "application/json"}:
        document = _text_document(raw, final_url, source_hint=source_hint, content_type=content_type or "text", max_chars=max_chars)
        _attach_redirect_metadata(document, requested_url=url, redirect_chain=redirect_chain)
        return document
    document = extract_html_document(
        raw,
        final_url,
        source_hint="wechat" if is_wechat_url(final_url, source_hint) else source_hint,
        max_chars=max_chars,
    )
    if source_hint and document.source == "web":
        document.source = source_hint
    _attach_redirect_metadata(document, requested_url=url, redirect_chain=redirect_chain)
    return document


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class RedirectBlockedError(UrlBlockedError):
    def __init__(self, message: str, redirect_chain: list[dict[str, str]]) -> None:
        super().__init__(message)
        self.redirect_chain = redirect_chain


class RedirectLimitError(RuntimeError):
    def __init__(self, max_redirects: int, redirect_chain: list[dict[str, str]]) -> None:
        super().__init__(f"redirect limit exceeded: {max_redirects}")
        self.redirect_chain = redirect_chain


def _fetch_bytes_with_redirects(
    url: str,
    *,
    allow_private_network: bool,
    timeout_sec: int,
    max_redirects: int = 5,
) -> tuple[bytes, str, str, list[dict[str, str]]]:
    ensure_url_allowed(url, allow_private_network=allow_private_network)
    current_url = url
    redirect_chain: list[dict[str, str]] = []
    opener = urllib.request.build_opener(NoRedirectHandler)
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/pdf,text/plain,*/*;q=0.8",
        "User-Agent": "Mozilla/5.0 ir_search/0.1",
    }
    for _ in range(max_redirects + 1):
        req = urllib.request.Request(current_url, headers=headers)
        try:
            with _open_once(opener, req, timeout_sec) as resp:
                raw = resp.read(5_000_000)
                content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
                return raw, content_type, current_url, redirect_chain
        except urllib.error.HTTPError as exc:
            if exc.code not in REDIRECT_STATUS_CODES:
                raise
            location = exc.headers.get("Location") if exc.headers else None
            if not location:
                raise
            next_url = urllib.parse.urljoin(current_url, location)
            redirect_chain.append({"from": current_url, "to": next_url, "status": str(exc.code)})
            try:
                ensure_url_allowed(next_url, allow_private_network=allow_private_network)
            except UrlBlockedError as blocked:
                raise RedirectBlockedError(str(blocked), redirect_chain) from blocked
            if len(redirect_chain) > max_redirects:
                raise RedirectLimitError(max_redirects, redirect_chain)
            current_url = next_url
    raise RedirectLimitError(max_redirects, redirect_chain)


def _open_once(opener, req: urllib.request.Request, timeout_sec: int):
    return opener.open(req, timeout=timeout_sec)


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


def _error_document(
    url: str,
    source_hint: Optional[str],
    error: str,
    *,
    content_type: str,
    redirect_chain: Optional[list[dict[str, str]]] = None,
) -> Document:
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
        extra={"source_text_trust": "untrusted", "redirect_chain": redirect_chain or []},
    )


def _attach_redirect_metadata(document: Document, *, requested_url: str, redirect_chain: list[dict[str, str]]) -> None:
    document.extra["requested_url"] = requested_url
    document.extra["redirect_chain"] = redirect_chain


REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}
