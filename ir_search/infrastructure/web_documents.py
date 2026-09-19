"""Shared deterministic public-document reading for retrieve and material search.

Rendering is optional; the source website, not the rendering library, owns evidence.
No crawling, LLM, login or automatic attachment downloads occur. Explicit
Scrapling mode selects a semantic body; explicit Firecrawl mode uses a cloud read.
"""
from __future__ import annotations

from dataclasses import replace
from enum import Enum
import hashlib
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, unquote

from ir_search.documents.models import hash_text
from ir_search.infrastructure.public_web import _url, _allowed_domain, fetch_public_document
from ir_search.registry import DataAdapterError
from ir_search.institutions import _institution_for_url
from ir_search.models import SourceTier, EvidenceType
from ir_search.infrastructure.web_toolkit import WEB_READ_MODES


class WebContentState(str, Enum):
    ARTICLE = "article_text"
    DIRECTORY = "directory_links"
    DOCUMENT = "document_text"
    LOADING = "loading"
    CHALLENGE = "challenge"
    IMAGE_ONLY = "image_only"
    ERROR = "extractor_error"
    EMPTY = "empty"


_VALID = {WebContentState.ARTICLE.value, WebContentState.DIRECTORY.value, WebContentState.DOCUMENT.value}
_DYNAMIC = {
    ("investor.nvidia.com", "/financial-info/financial-reports/default.aspx"),
    ("investor.apple.com", "/investor-relations/"),
}


def _dynamic_directory(url):
    parsed = urlsplit(url)
    return (parsed.hostname, parsed.path) in _DYNAMIC


def _link_kind(parsed, link):
    if link.get('rel') == 'embedded_document':
        return 'pdf'
    names = [unquote(parsed.path), str(link.get('download') or ''), str(link.get('text') or '')]
    names.extend(value for key, value in parse_qsl(parsed.query) if key.lower() in {'filename', 'file', 'name'})
    if any(re.search(r'\.pdf$', value, re.I) for value in names):
        return 'pdf'
    if any(re.search(r'\.(?:docx?|xlsx?|csv|zip)$', value, re.I) for value in names):
        return 'attachment'
    return 'web_page'


def _links(document):
    seen, result = set(), []
    for link in document.links:
        if link.get("rel") not in {"href", "embedded_document"}:
            continue
        try:
            parsed, _, _ = _url(link.get("href"))
        except DataAdapterError:
            continue
        url = urlunsplit(parsed._replace(fragment=""))
        if '{{' in unquote(url) or '}}' in unquote(url):
            continue  # Unrendered template expressions are not discovered URLs.
        if url in seen:
            continue
        seen.add(url)
        result.append({"url": url, "text": str(link.get("text") or "")[:1000],
                       "kind": _link_kind(parsed, link),
                       "status": "discovered_not_retrieved"})
    # Prefer attachments in a bounded directory response without downloading any.
    result.sort(key=lambda link: {'pdf': 0, 'attachment': 1, 'web_page': 2}[link['kind']])
    return result[:100], len(result) > 100 or document.extra.get("links_truncated", False)


def _state(document, links):
    text = document.text.strip()
    head = text[:3000].lower()
    title = document.title.lower()
    if document.errors or re.search(r"crawl4ai error\s*:|error message:\s*invalid tag", head):
        return WebContentState.ERROR
    challenge = ("verify you are human", "verify that you are human", "checking your browser",
                 "access denied", "just a moment", "人机验证", "访问验证", "安全验证", "请输入验证码")
    if any(term in title for term in challenge) or (len(text) < 4000 and any(term in head for term in challenge)):
        return WebContentState.CHALLENGE
    if len(text) < 1000 and re.search(r"\bcaptcha\b", head):
        return WebContentState.CHALLENGE
    institution = _institution_for_url(document.url)
    if institution and institution.category == 'regulator':
        if (re.search(r'\{\{\s*(?:rulesTitle|caption|item\.|x\.)', text)
                or institution.institution_id == 'nfra' and document.content_type == 'html'
                and re.search(r'\{\{[^{}]{1,160}\}\}', text)):
            return WebContentState.LOADING
        directory_paths = {urlsplit(url).path for url in institution.directory_urls}
        if urlsplit(document.url).path in directory_paths:
            return WebContentState.DIRECTORY if links else WebContentState.LOADING
    if sum(link["kind"] == "pdf" for link in links) >= 3:
        return WebContentState.DIRECTORY
    if (re.search(r"\bloading(?:\.{2,}|\s+(?:financial|reports|data|content))|加载中|正在加载", head)
            or (_dynamic_directory(document.url) and not any(link["kind"] == "pdf" for link in links))):
        return WebContentState.LOADING
    if not text:
        if document.extra.get("script_count"):
            return WebContentState.LOADING
        return WebContentState.IMAGE_ONLY if document.extra.get("image_count") else WebContentState.EMPTY
    if len(text) < 350 and document.extra.get("image_count") and re.search(r"图解|一图读懂|infographic", title, re.I):
        return WebContentState.IMAGE_ONLY
    if document.content_type == "pdf" or "pdf" in document.extraction_method:
        return WebContentState.DOCUMENT
    return WebContentState.ARTICLE


def _annotate(document, backend):
    links, truncated = _links(document)
    state = _state(document, links)
    details = {"backend": backend, "content_state": state.value, "assessment": "rule_based_not_completeness_verification",
               "raw_hash": document.raw_hash, "text_hash": hashlib.sha256(document.text.encode()).hexdigest(),
               "links_truncated": bool(truncated), "publication_metadata": document.extra.get("publication_metadata", {}),
               "attempts": [{"backend": backend, "state": state.value}]}
    document.extra.update(web_read=details, public_links=links)
    for key in ('html_extraction', 'firecrawl'):
        if key in document.extra:
            details[key] = document.extra[key]
    institution = _institution_for_url(document.url)
    if institution:
        details.update(institution=institution.to_dict(), institution_match_basis='official_domain_not_content_verification')
        document.source_tier = SourceTier.REGULATOR if institution.category == 'regulator' else SourceTier.COMPANY
    if state == WebContentState.DIRECTORY:
        document.evidence_type = EvidenceType.UNKNOWN
        document.warnings.append('web_directory_listing_not_document_body')
        details['attachment_count'] = sum(link['kind'] in {'pdf', 'attachment'} for link in links)
        details['directory_completeness'] = 'not_established_no_pagination'
    if state.value not in _VALID:
        document.warnings.append("web_content_" + state.value)
    if truncated:
        document.warnings.append("web_links_truncated")
    from .official_materials import _official_identity
    identity = _official_identity(document.url)
    if identity:
        document.source_tier = SourceTier.EXCHANGE_FILING if identity[0] == 'hkex' else SourceTier.COMPANY
        document.evidence_type = EvidenceType.ANNOUNCEMENT if identity[0] == 'hkex' else EvidenceType.UNKNOWN
        document.extra['publisher'] = identity[1]
    return document


def read_web_document(url: str, *, context, max_chars: int = 20000, allowed_domains=(), mode="auto"):
    """Read one URL with shared diagnostics; auto renders only an observed dynamic gap.

    HTTP/TLS/auth/policy failures never trigger a browser retry. Callers must inspect
    web_read.content_state before using text; unsuccessful rendering keeps HTTP text
    with explicit diagnostics, never relabels it as successfully rendered content.
    """
    if type(max_chars) is not int or not 1 <= max_chars <= 100000:
        raise ValueError("Invalid text limit")
    if mode not in WEB_READ_MODES:
        raise ValueError("Invalid web read mode")
    if not isinstance(allowed_domains, tuple) or any(not isinstance(d, str) or not d for d in allowed_domains):
        raise ValueError("Invalid domain restriction")
    _, host, _ = _url(url)
    if not _allowed_domain(host, allowed_domains):
        raise DataAdapterError("blocked_url")
    context.check_active()
    # Assess a useful amount of source text before caller truncation (even max_chars=1).
    if mode == 'firecrawl':
        from .web_toolkit import fetch_firecrawl_document
        document = _annotate(fetch_firecrawl_document(url, context=context, max_chars=100000,
                             allowed_domains=allowed_domains), 'firecrawl')
    else:
        options = {'html_parser': 'scrapling'} if mode == 'scrapling' else {}
        document = _annotate(fetch_public_document(url, context=context, max_chars=100000,
                             allowed_domains=allowed_domains, allow_empty=True, **options), 'http')
    state = document.extra["web_read"]["content_state"]
    should_render = (mode == "browser" or mode == "auto" and state == "loading")
    # A forced browser request is still not permission to bypass a challenge/error.
    if should_render and state not in {"challenge", "extractor_error", "image_only"} and document.content_type != "pdf":
        attempts = document.extra["web_read"]["attempts"]
        from ir_search.infrastructure.web_browser import render_public_document
        try:
            rendered = _annotate(render_public_document(document.url, context=context,
                                   allowed_domains=allowed_domains), "crawl4ai")
            render_state = rendered.extra["web_read"]["content_state"]
            attempts.extend(rendered.extra["web_read"]["attempts"])
            if render_state in _VALID:
                document = rendered
            else:
                document.warnings.append("web_browser_content_unavailable")
            document.extra["web_read"]["attempts"] = attempts
            document.extra["web_read"]["browser"] = rendered.extra.get("browser_diagnostics", {})
        except DataAdapterError as exc:
            attempts.append({"backend": "crawl4ai", "state": exc.code})
            document.warnings.append("web_browser_" + exc.code)
        context.check_active()
    text = document.text[:max_chars]
    warnings = list(dict.fromkeys(document.warnings))
    if len(document.text) > max_chars:
        warnings.append("text_truncated")
    details = {**document.extra["web_read"], "text_hash": hashlib.sha256(text.encode()).hexdigest()}
    return replace(document, text=text, text_hash=hash_text(text), warnings=warnings,
                   extra={**document.extra, "web_read": details})
