from __future__ import annotations

from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any, Optional
from urllib.parse import urljoin

from ir_search.models import EvidenceType, SourceTier

from .models import Document, hash_bytes, hash_text, make_doc_id, utc_now


class ArticleHTMLParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title = ""
        self.canonical_url = base_url
        self.meta: dict[str, str] = {}
        self.links: list[dict[str, Any]] = []
        self._tag_stack: list[str] = []
        self._skip_depth = 0
        self._title_depth = 0
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        attr = {key.lower(): value or "" for key, value in attrs}
        self._tag_stack.append(tag)
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
        if tag == "title":
            self._title_depth += 1
        if tag == "meta":
            name = (attr.get("name") or attr.get("property") or "").lower()
            content = attr.get("content") or ""
            if name and content:
                self.meta[name] = content.strip()
        if tag == "link":
            rel = attr.get("rel", "").lower()
            href = attr.get("href", "")
            if href:
                absolute = urljoin(self.base_url, href)
                self.links.append({"rel": rel, "href": absolute})
                if rel == "canonical":
                    self.canonical_url = absolute
        if tag == "a" and attr.get("href"):
            self.links.append({"rel": "href", "href": urljoin(self.base_url, attr["href"])})
        if tag in BLOCK_TAGS:
            self._text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in BLOCK_TAGS:
            self._text_parts.append("\n")
        if tag == "title" and self._title_depth:
            self._title_depth -= 1
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1
        if self._tag_stack:
            self._tag_stack.pop()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        cleaned = " ".join(unescape(data).split())
        if not cleaned:
            return
        if self._title_depth:
            self.title = f"{self.title} {cleaned}".strip()
        self._text_parts.append(cleaned)

    def extracted_text(self) -> str:
        lines = [" ".join(line.split()) for line in "\n".join(self._text_parts).splitlines()]
        useful = [line for line in lines if line]
        return "\n\n".join(useful)


def extract_html_document(
    raw: bytes,
    url: str,
    *,
    source_hint: Optional[str] = None,
    max_chars: int = 20000,
) -> Document:
    """Extract title, canonical URL, links, and text from HTML bytes."""

    text = raw.decode("utf-8", errors="replace")
    parser = ArticleHTMLParser(url)
    warnings: list[str] = []
    errors: list[str] = []
    try:
        parser.feed(text)
    except Exception as exc:
        errors.append(f"html parse failed: {exc}")
    extracted = parser.extracted_text()
    if len(extracted) > max_chars:
        extracted = extracted[:max_chars].rstrip()
        warnings.append(f"text truncated to max_chars={max_chars}")
    if len(extracted) < 80:
        warnings.append("extracted text is short")
    title = (
        parser.meta.get("og:title")
        or parser.meta.get("twitter:title")
        or parser.title
        or parser.canonical_url
        or url
    )
    published_at = parse_datetime(
        parser.meta.get("article:published_time")
        or parser.meta.get("pubdate")
        or parser.meta.get("date")
        or parser.meta.get("publishdate")
    )
    raw_hash = hash_bytes(raw)
    text_hash = hash_text(extracted)
    content_type = "wechat" if "mp.weixin.qq.com" in url or source_hint == "wechat" else "html"
    source = source_hint or ("wechat" if content_type == "wechat" else "web")
    return Document(
        doc_id=make_doc_id(parser.canonical_url or url, text_hash),
        url=url,
        canonical_url=parser.canonical_url or url,
        title=title.strip(),
        source=source,
        source_tier=SourceTier.MEDIA,
        evidence_type=EvidenceType.OPINION if content_type == "wechat" else EvidenceType.NEWS,
        content_type=content_type,
        published_at=published_at,
        fetched_at=utc_now(),
        extraction_method="stdlib_html_parser",
        text=extracted,
        links=parser.links[:100],
        raw_hash=raw_hash,
        text_hash=text_hash,
        warnings=warnings,
        errors=errors,
        extra={"source_text_trust": "untrusted"},
    )


def parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    cleaned = value.strip()
    try:
        return datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        return None


BLOCK_TAGS = {
    "article",
    "aside",
    "blockquote",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "figcaption",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "li",
    "main",
    "nav",
    "p",
    "pre",
    "section",
    "table",
    "td",
    "th",
    "tr",
    "ul",
}
