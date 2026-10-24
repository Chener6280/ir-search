"""Research material contracts: channels, content kinds and dates are separate."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional
from urllib.parse import parse_qsl, urlsplit

from . import AdapterMode, Diagnostic, JsonModel, Provenance, Status, _day, _instant, _strings, _text


class MaterialKind(str, Enum):
    AUDIO = "audio"
    VIDEO = "video"
    DOCUMENT = "document"
    NOTE = "note"
    SOCIAL_POST = "social_post"
    WEB_PAGE = "web_page"
    ANNOUNCEMENT = "announcement"
    RESEARCH_REPORT = "research_report"
    CALL_TRANSCRIPT = "call_transcript"
    CHANNEL_CHECK = "channel_check"
    NEWS = "news"
    POLICY = "policy"
    QA = "qa"
    OPINION = "opinion"


class TextScope(str, Enum):
    METADATA = "metadata"
    SEARCH_SNIPPET = "search_snippet"
    SOURCE_EXCERPT = "source_excerpt"
    ABSTRACT = "abstract"
    EXTRACTED_TEXT = "extracted_text"


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value):
        raise ValueError("Expected a source identifier")
    return value


def _reference(value, *, web_only=False):
    _text(value, "reference")
    parsed = urlsplit(value)
    forbidden = {"token", "apikey", "api_key", "key", "access_token", "password", "authorization", "secret", "signature", "x-amz-signature", "xsec_token", "xsectoken"}
    if (not parsed.scheme or not parsed.netloc or parsed.username or parsed.password
            or any(k.lower() in forbidden for k, _ in parse_qsl(parsed.query))
            or (web_only and parsed.scheme not in {"http", "https"})
            or any(ord(c) < 32 for c in value)):
        raise ValueError("Expected a credential-free source reference")
    return value


def _bounds(instance, start_name, end_name):
    start, end = (_day(getattr(instance, name)) for name in (start_name, end_name))
    if bool(start) != bool(end) or (start and (start > end or end == date.max)):
        raise ValueError("Dates require an ordered pair")
    object.__setattr__(instance, start_name, start)
    object.__setattr__(instance, end_name, end)


def _video_languages(values):
    if (not isinstance(values, (tuple, list)) or not 1 <= len(values) <= 8
            or any(not isinstance(v, str) or not re.fullmatch(r'[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8}){0,2}', v) for v in values)):
        raise ValueError('Invalid caption languages')
    return tuple(dict.fromkeys(values))


@dataclass(frozen=True)
class MaterialSearchRequest(JsonModel):
    question: str
    symbols: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    published_start: Optional[date] = None
    published_end: Optional[date] = None
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    material_types: tuple[MaterialKind, ...] = ()
    providers: tuple[str, ...] = ()
    exclude_providers: tuple[str, ...] = ()
    limit: int = 20
    max_sources: int = 4
    candidates_per_source: int = 20
    text_reads_per_source: int = 3
    max_chars: int = 20000
    web_region: str = "auto"
    wechat_accounts: tuple[str, ...] = ()
    ima_knowledge_base_ids: tuple[str, ...] = ()
    ima_include_notes: bool = True
    web_read_mode: str = "auto"
    wechat_cache_mode: str = "use"
    wisburg_categories: tuple[str, ...] = ()
    dry_run: bool = False
    web_institutions: tuple[str, ...] = ()
    web_read_workers: int = 1
    video_platforms: tuple[str, ...] = ('bilibili', 'youtube')
    video_languages: tuple[str, ...] = ('zh-Hans', 'zh-CN', 'zh', 'en')
    zsxq_group_ids: tuple[str, ...] = ()
    source_cursors: tuple[str, ...] = ()
    sec_ciks: tuple[str, ...] = ()
    sec_forms: tuple[str, ...] = ()
    xhs_sort: str = 'latest'
    xhs_comment_limit: int = 0
    xhs_cache_mode: str = 'use'

    def __post_init__(self):
        _text(self.question, "question")
        if self.xhs_sort not in {'latest','relevance','likes'} or self.xhs_cache_mode not in {'use','refresh'}:
            raise ValueError('Invalid XHS options')
        if type(self.xhs_comment_limit) is not int or not 0<=self.xhs_comment_limit<=19:
            raise ValueError('Invalid XHS comment budget')
        from ir_search.infrastructure.sec import _cik
        ciks = _strings(self.sec_ciks, 'sec_ciks')
        if len(ciks) > 5: raise ValueError('At most five SEC CIKs')
        object.__setattr__(self, 'sec_ciks', tuple(dict.fromkeys(_cik(v) for v in ciks)))
        forms = _strings(self.sec_forms, 'sec_forms')
        if len(forms) > 20 or any(not re.fullmatch(r'[A-Z0-9][A-Z0-9 -]{0,20}(?:/A)?', v) for v in forms):
            raise ValueError('Invalid SEC forms')
        object.__setattr__(self, 'sec_forms', forms)
        groups = _strings(self.zsxq_group_ids, 'zsxq_group_ids')
        if len(groups) > 20 or any(not re.fullmatch(r'[1-9][0-9]{0,29}',v) for v in groups):
            raise ValueError('Invalid group IDs')
        object.__setattr__(self, 'zsxq_group_ids', groups)
        # Encoded cursors have their own 12,000-character bound; ordinary search
        # strings have a smaller 2,000-character limit and must not truncate them.
        cursors = self.source_cursors
        if (not isinstance(cursors, (list, tuple)) or len(cursors) > 20
                or any(not isinstance(v, str) for v in cursors) or len(set(cursors)) != len(cursors)):
            raise ValueError('Invalid source cursors')
        from ir_search.infrastructure.material_cursor import _decode
        try:
            for token in cursors:
                d = _decode(token)
                if d['provider'] not in self.providers: raise ValueError('Cursor provider must be explicit')
        except Exception: raise ValueError('Invalid material source cursor') from None
        object.__setattr__(self, 'source_cursors', tuple(cursors))

        platforms = _strings(self.video_platforms, 'video_platforms')
        if not platforms or len(platforms) > 2 or not set(platforms) <= {'bilibili', 'youtube'}:
            raise ValueError('Invalid video platforms')
        object.__setattr__(self, 'video_platforms', platforms)
        object.__setattr__(self, 'video_languages', _video_languages(self.video_languages))
        if type(self.dry_run) is not bool:
            raise ValueError('dry_run must be boolean')
        ids = _strings(self.web_institutions, 'web_institutions')
        if len(ids) > 8:
            raise ValueError('At most eight institution scopes')
        from ir_search.institutions import _selected_institutions
        _selected_institutions(ids)
        object.__setattr__(self, 'web_institutions', ids)
        categories = _strings(self.wisburg_categories, 'wisburg_categories')
        if not set(categories) <= {'ib','company','am','archive','ec','feed','market_daily','article','mikko'}:
            raise ValueError('Invalid Wisburg categories')
        object.__setattr__(self, 'wisburg_categories', categories)
        from ir_search.infrastructure.web_toolkit import WEB_READ_MODES
        if self.web_read_mode not in WEB_READ_MODES:
            raise ValueError("Invalid web read mode")
        if self.wechat_cache_mode not in ("use", "refresh", "off"):
            raise ValueError("Invalid WeChat cache mode")
        ids = _strings(self.ima_knowledge_base_ids, "ima_knowledge_base_ids")
        if len(ids) > 20 or any(not re.fullmatch(r"[A-Za-z0-9_+=.-]{1,512}", v) for v in ids):
            raise ValueError("Invalid IMA knowledge base IDs")
        object.__setattr__(self, "ima_knowledge_base_ids", ids)
        if type(self.ima_include_notes) is not bool: raise ValueError("Invalid IMA notes flag")
        if not isinstance(self.web_region, str) or self.web_region not in {"auto", "cn", "overseas", "both"}:
            raise ValueError("Invalid web region")
        for name in ("symbols", "entities", "keywords", "providers", "exclude_providers", "wechat_accounts"):
            values = _strings(getattr(self, name), name)
            if len(values) > 20 or any(len(value) > 100 for value in values):
                raise ValueError("Too many or oversized search terms")
            object.__setattr__(self, name, values)
        for symbol in self.symbols:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.\-]{0,29}", symbol):
                raise ValueError("Invalid symbol")
        for source in self.providers + self.exclude_providers:
            _identifier(source)
        if set(self.providers) & set(self.exclude_providers):
            raise ValueError("A provider cannot be both included and excluded")
        for start, end in (("published_start", "published_end"), ("period_start", "period_end")):
            _bounds(self, start, end)
        if self.published_start and (self.published_end - self.published_start).days > 366:
            raise ValueError("Publication window must be at most 367 calendar days")
        kinds = tuple(MaterialKind(v) for v in _strings(self.material_types, "material_types"))
        object.__setattr__(self, "material_types", kinds)
        for name, low, high in (("limit", 1, 50), ("max_sources", 1, 8), ("candidates_per_source", 1, 50),
                                ("text_reads_per_source", 0, 10), ("max_chars", 1, 50000), ("web_read_workers", 1, 4)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"Search budget out of range: {name} must be an integer between {low} and {high}")


@dataclass(frozen=True)
class MaterialCapability(JsonModel):
    provider: str
    channel: str
    material_types: tuple[MaterialKind, ...]
    account_scope: str = "default"
    adapter_mode: AdapterMode = AdapterMode.LIVE
    requires_symbols: bool = False
    max_symbols: int = 20
    supports_publication_filter: bool = True
    search_basis: str = "bounded_records_local_matching"
    coverage_notes: tuple[str, ...] = ()
    allows_stored_summaries: bool = False

    def __post_init__(self):
        _identifier(self.provider)
        if type(self.allows_stored_summaries) is not bool:
            raise ValueError('Invalid stored summary capability')
        _identifier(self.channel)
        _text(self.account_scope, "account_scope")
        kinds = tuple(MaterialKind(v) for v in _strings(self.material_types, "material_types"))
        if not kinds:
            raise ValueError("Declare at least one material type")
        object.__setattr__(self, "material_types", kinds)
        object.__setattr__(self, "adapter_mode", AdapterMode(self.adapter_mode))
        object.__setattr__(self, "coverage_notes", _strings(self.coverage_notes, "coverage_notes"))
        if type(self.max_symbols) is not int or not 1 <= self.max_symbols <= 20:
            raise ValueError("Invalid symbol capacity")
        if type(self.requires_symbols) is not bool or type(self.supports_publication_filter) is not bool:
            raise ValueError("Capability flags must be boolean")
        _identifier(self.search_basis)


@dataclass(frozen=True)
class MaterialAttachment(JsonModel):
    source_ref: str
    name: str
    media_type: str = "unknown"
    size_bytes: Optional[int] = None
    status: str = "metadata_only"

    def __post_init__(self):
        _reference(self.source_ref)
        _text(self.name, "attachment name")
        if len(self.name) > 1000 or self.media_type not in {"pdf", "unknown"} or self.status != "metadata_only":
            raise ValueError("Invalid attachment metadata")
        if self.size_bytes is not None and (type(self.size_bytes) is not int or self.size_bytes < 0):
            raise ValueError("Invalid attachment size")


@dataclass(frozen=True)
class MaterialSection(JsonModel):
    role: str
    author: str
    source_ref: str
    start_char: int
    end_char: int
    published_at: Optional[datetime] = None

    def __post_init__(self):
        if self.role not in {"post", "question", "answer", "comment", "unverified"} or not isinstance(self.author, str):
            raise ValueError("Invalid material section")
        _reference(self.source_ref)
        if (type(self.start_char) is not int or type(self.end_char) is not int
                or not 0 <= self.start_char < self.end_char <= 100000):
            raise ValueError("Invalid section offsets")
        object.__setattr__(self, "published_at", _instant(self.published_at))


@dataclass(frozen=True)
class MaterialCandidate(JsonModel):
    source_ref: str
    title: str
    material_type: MaterialKind
    channel: str
    provenance: Provenance
    text: str = ""
    text_scope: TextScope = TextScope.METADATA
    original_url: Optional[str] = None
    symbols: tuple[str, ...] = ()
    published_on: Optional[date] = None
    published_at: Optional[datetime] = None
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    warnings: tuple[str, ...] = ()
    authors: tuple[str, ...] = ()
    source_document_id: Optional[str] = None
    discovery_provider: Optional[str] = None
    collection_id: Optional[str] = None
    collection_name: Optional[str] = None
    source_record_type: Optional[str] = None
    attachments: tuple[MaterialAttachment, ...] = ()
    sections: tuple[MaterialSection, ...] = ()
    text_provider: Optional[str] = None
    source_created_at: Optional[datetime] = None
    source_updated_at: Optional[datetime] = None
    links: tuple[dict, ...] = ()
    read_details: dict = field(default_factory=dict)
    article: dict = field(default_factory=dict)

    def __post_init__(self):
        _reference(self.source_ref)
        if not isinstance(self.links, tuple) or len(self.links) > 100:
            raise ValueError("Invalid public links")
        for link in self.links:
            if not isinstance(link, dict) or not isinstance(link.get("text", ""), str):
                raise ValueError("Invalid public link")
            _reference(link.get("url"), web_only=True)
        if not isinstance(self.read_details, dict):
            raise ValueError("Invalid read details")
        if not isinstance(self.article, dict): raise ValueError('Invalid article structure')
        if self.original_url is not None:
            _reference(self.original_url, web_only=True)
        _text(self.title, "title")
        _identifier(self.channel)
        if not isinstance(self.provenance, Provenance):
            raise ValueError("Typed provenance required")
        object.__setattr__(self, "material_type", MaterialKind(self.material_type))
        object.__setattr__(self, "text_scope", TextScope(self.text_scope))
        if not isinstance(self.text, str) or len(self.text) > 100000:
            raise ValueError("Material text exceeds contract limit")
        if (self.text_scope == TextScope.METADATA and self.text) or (self.text_scope != TextScope.METADATA and not self.text.strip()):
            raise ValueError("Text scope does not match content")
        object.__setattr__(self, "symbols", _strings(self.symbols, "symbols"))
        object.__setattr__(self, "published_on", _day(self.published_on))
        instant = _instant(self.published_at)
        object.__setattr__(self, "published_at", instant)
        object.__setattr__(self, "source_created_at", _instant(self.source_created_at))
        object.__setattr__(self, "source_updated_at", _instant(self.source_updated_at))
        if instant and (self.published_on is None or instant.date() != self.published_on):
            raise ValueError("Publication date and timestamp disagree")
        _bounds(self, "period_start", "period_end")
        object.__setattr__(self, "warnings", _strings(self.warnings, "warnings"))
        for warning in self.warnings:
            _identifier(warning)
        object.__setattr__(self, "authors", _strings(self.authors, "authors"))
        if self.source_document_id is not None:
            _text(self.source_document_id, "source_document_id")
        if self.discovery_provider is not None:
            _identifier(self.discovery_provider)
        if self.text_provider is not None:
            _identifier(self.text_provider)
        for name in ("collection_id", "collection_name", "source_record_type"):
            if getattr(self, name) is not None:
                _text(getattr(self, name), name)
        if not isinstance(self.attachments, tuple) or len(self.attachments) > 50 or any(not isinstance(a, MaterialAttachment) for a in self.attachments):
            raise ValueError("Invalid attachments")
        if not isinstance(self.sections, tuple) or len(self.sections) > 20:
            raise ValueError("Invalid sections")
        end = 0
        for section in self.sections:
            if not isinstance(section, MaterialSection) or section.start_char < end or section.end_char > len(self.text):
                raise ValueError("Sections must address ordered, non-overlapping returned text")
            end = section.end_char


@dataclass(frozen=True)
class MaterialSourceScan(JsonModel):
    """Actual subquery coverage; received rows can exceed locally inspected rows."""
    operation: str
    material_type: MaterialKind
    query_start: date
    query_end: date
    state: str
    received_count: int = 0
    inspected_count: int = 0
    publisher_filter: Optional[str] = None
    symbols: tuple[str, ...] = ()
    date_filter_basis: str = "upstream"
    collection_id: Optional[str] = None
    has_more: Optional[bool] = None
    next_cursor: Optional[str] = None
    discovery_provider: Optional[str] = None
    web_region: Optional[str] = None
    routing_basis: Optional[str] = None
    search_query: Optional[str] = None
    fetched_at: Optional[datetime] = None
    cache_state: Optional[str] = None

    def __post_init__(self):
        object.__setattr__(self, 'fetched_at', _instant(self.fetched_at))
        if self.cache_state not in (None, 'hit', 'fresh'): raise ValueError('Invalid scan cache state')
        _identifier(self.operation)
        if self.search_query is not None:
            _text(self.search_query, "search_query")
            if len(self.search_query)>1000: raise ValueError("Oversized search query")
        _identifier(self.state)
        object.__setattr__(self, "material_type", MaterialKind(self.material_type))
        _bounds(self, "query_start", "query_end")
        if not self.query_start:
            raise ValueError("Scan bounds required")
        if (type(self.received_count) is not int or type(self.inspected_count) is not int
                or not 0 <= self.inspected_count <= self.received_count <= 1000):
            raise ValueError("Invalid scan counts")
        if self.publisher_filter is not None:
            _text(self.publisher_filter, "publisher_filter")
        object.__setattr__(self, "symbols", _strings(self.symbols, "symbols"))
        if self.date_filter_basis not in {"upstream", "local_publication_metadata"}:
            raise ValueError("Invalid date filtering basis")
        if self.collection_id is not None: _text(self.collection_id, "collection_id")
        if self.has_more is not None and type(self.has_more) is not bool: raise ValueError("Invalid page flag")
        if self.next_cursor is not None:
            _text(self.next_cursor, "next_cursor")
            if len(self.next_cursor) > 512: raise ValueError("Oversized cursor")
        for name in ("discovery_provider", "routing_basis"):
            if getattr(self, name) is not None: _identifier(getattr(self, name))
        if self.web_region is not None and self.web_region not in {"cn", "overseas"}:
            raise ValueError("Invalid scan region")


@dataclass(frozen=True)
class WebSearchFallback(JsonModel):
    """Pending handoff to a caller's native search, never a completed search result."""
    failed_provider: str
    query: str
    web_region: str
    published_start: date
    published_end: date
    max_results: int
    allowed_domains: tuple[str, ...] = ()
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    reason: str = field(default='quota', init=False)
    action: str = field(default='caller_web_search', init=False)
    state: str = field(default='pending_caller', init=False)
    after_search: str = field(default='retrieve', init=False)
    max_attempts: int = field(default=1, init=False)
    max_text_reads: int = 0

    def __post_init__(self):
        if self.failed_provider not in {'bocha', 'anysearch', 'exa', 'tavily', 'firecrawl', 'searxng'}:
            raise ValueError('Invalid web discovery provider')
        if not isinstance(self.query, str) or not self.query.strip() or len(self.query) > 4000:
            raise ValueError('Invalid fallback query')
        if self.web_region not in {'cn', 'overseas'}:
            raise ValueError('Invalid fallback region')
        _bounds(self, 'published_start', 'published_end')
        _bounds(self, 'period_start', 'period_end')
        if self.published_start is None or type(self.max_results) is not int or not 1 <= self.max_results <= 10:
            raise ValueError('Invalid fallback scope')
        if type(self.max_text_reads) is not int or not 0 <= self.max_text_reads <= self.max_results:
            raise ValueError('Invalid fallback reading budget')
        if (not isinstance(self.allowed_domains, tuple) or len(self.allowed_domains) > 20
                or any(not isinstance(d, str) or len(d) > 253 or '..' in d
                       or not re.fullmatch(r'[a-z0-9]+(?:[.-][a-z0-9]+)*\.[a-z]{2,}', d)
                       for d in self.allowed_domains)):
            raise ValueError('Invalid fallback domains')


@dataclass
class MaterialSearchPage(JsonModel):
    candidates: list[MaterialCandidate]
    scanned_count: int
    complete: bool = False
    diagnostics: list[Diagnostic] = field(default_factory=list)
    scans: list[MaterialSourceScan] = field(default_factory=list)
    continuation_cursors: list[str] = field(default_factory=list)
    fallback_requests: list[WebSearchFallback] = field(default_factory=list)


@dataclass
class MaterialSearchResult(JsonModel):
    request: MaterialSearchRequest
    request_id: str
    status: Status = Status.UNAVAILABLE
    items: list[dict] = field(default_factory=list)
    plan: dict = field(default_factory=dict)
    coverage: list[dict] = field(default_factory=list)
    gaps: list[dict] = field(default_factory=list)
    required_inputs: list[str] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    complete: bool = False
    schema_version: str = "1.0"
    source_text_trust: str = "untrusted"
    audit: dict = field(default_factory=dict)
    fallback_requests: list[WebSearchFallback] = field(default_factory=list)
    # Wall-clock observations. Like request_id they differ between identical requests, so they
    # live apart from items/coverage, which stay comparable across runs.
    timing: dict = field(default_factory=dict)
