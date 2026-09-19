"""Versioned, provider-independent contracts for data and research material."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from ir_search.models import EvidenceType, FailureKind, SourceAuthority, SourceTier

SCHEMA_VERSION = "1.0"


class Status(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class ValueKind(str, Enum):
    ACTUAL = "actual"
    ESTIMATE = "estimate"
    CONSENSUS = "consensus"
    ASSUMPTION = "assumption"
    DERIVED = "derived"


class AdapterMode(str, Enum):
    LIVE = "live"
    MOCK = "mock"
    PLACEHOLDER = "placeholder"
    FALLBACK = "fallback"
    UNKNOWN = "unknown"


class AccessStatus(str, Enum):
    UNKNOWN = "unknown"
    GRANTED = "granted"
    DENIED = "denied"


def _json_value(value: Any) -> Any:
    if isinstance(value, SourceTier):
        return value.name
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _json_value(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Non-finite decimal is not valid research data")
        return str(value)  # Preserve financial precision across JSON transports.
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite float is not valid research data")
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings")
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise TypeError("Unsupported value in research response")


class JsonModel:
    def to_dict(self) -> dict[str, Any]:
        """Serialize strictly; never stringify opaque objects or non-finite numbers."""
        return _json_value(self)


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise ValueError(f"{name} must be a nonempty string of at most 2000 characters")


def _strings(values, name: str) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)) or len(values) > 200:
        raise ValueError(f"{name} must be a list or tuple with at most 200 entries")
    for value in values:
        _text(value, name)
    if len(set(values)) != len(values):
        raise ValueError(f"{name} must not contain duplicate entries")
    return tuple(values)


def _day(value) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        raise ValueError("Date fields require a calendar date, not a timestamp")
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Date fields require ISO YYYY-MM-DD")
    return date.fromisoformat(value)


def _instant(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Timestamps require an explicit timezone")
    return value


@dataclass(frozen=True)
class Diagnostic(JsonModel):
    code: str
    operation: str
    provider: Optional[str] = None
    failure_kind: FailureKind = FailureKind.NONE
    message: str = ""
    adapter_mode: AdapterMode = AdapterMode.UNKNOWN
    datasets: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.datasets, (tuple, list)) or any(not isinstance(v, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", v) for v in self.datasets):
            raise ValueError("Invalid diagnostic dataset scope")
        object.__setattr__(self, "datasets", tuple(self.datasets))
        for name in ("code", "operation"):
            value = getattr(self, name)
            if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value):
                raise ValueError("Diagnostic codes and operations require stable identifiers")
        object.__setattr__(self, "failure_kind", FailureKind(self.failure_kind))
        object.__setattr__(self, "adapter_mode", AdapterMode(self.adapter_mode))


@dataclass(frozen=True)
class Provenance(JsonModel):
    provider: str
    publisher: str
    fetched_at: datetime
    authority: SourceAuthority = SourceAuthority.UNKNOWN
    source_tier: Optional[SourceTier] = None
    evidence_type: EvidenceType = EvidenceType.DATA_TABLE
    adapter_mode: AdapterMode = AdapterMode.UNKNOWN
    generated: bool = False

    def __post_init__(self):
        _text(self.provider, "provider")
        _text(self.publisher, "publisher")
        object.__setattr__(self, "fetched_at", _instant(self.fetched_at))
        if self.fetched_at is None:
            raise ValueError("fetched_at is required")
        object.__setattr__(self, "adapter_mode", AdapterMode(self.adapter_mode))
        object.__setattr__(self, "authority", SourceAuthority(self.authority))
        object.__setattr__(self, "evidence_type", EvidenceType(self.evidence_type))
        if self.source_tier is not None:
            object.__setattr__(self, "source_tier", SourceTier(self.source_tier))
        if not isinstance(self.generated, bool):
            raise ValueError("generated must be boolean")


@dataclass(frozen=True)
class DataRequest(JsonModel):
    dataset: str
    symbols: tuple[str, ...] = ()
    fields: tuple[str, ...] = ()
    start: Optional[date] = None
    end: Optional[date] = None
    market: str = "A_SHARE"
    frequency: Optional[str] = None
    adjustment: Optional[str] = None
    value_kind: ValueKind = ValueKind.ACTUAL
    as_of: Optional[datetime] = None
    provider: Optional[str] = None
    limit: int = 1000
    cursor: Optional[str] = None
    allow_partial: bool = True
    statement: str = "all"
    statement_scope: str = "consolidated"
    period_basis: str = "cumulative"
    revision: str = "original"

    def __post_init__(self):
        if self.frequency is None:
            default = "snapshot" if self.dataset in {"securities", "futures_contracts", "options_contracts"} else "report" if self.dataset == "financial_statements" else "1m" if self.dataset.endswith("_intraday") else "1d"
            if self.dataset == "fund_profile": default = "snapshot"
            if self.dataset == "fund_holdings": default = "report"
            if self.dataset == "macro_series": default = "native"
            if self.dataset == "derivatives_bars": default = "5m"
            if self.dataset == "financial_statements_standardized":
                default = "annual"
            object.__setattr__(self, "frequency", default)
        if self.adjustment is None:
            default = "none" if self.dataset in {"securities", "futures_contracts", "options_contracts", "trading_calendar", "financial_statements", "financial_statements_standardized"} else "raw"
            if self.dataset.startswith("fund_") or self.dataset in {"macro_series", "derivatives_bars", "option_risk"}: default = "none"
            object.__setattr__(self, "adjustment", "source_unspecified" if self.dataset == "prices_daily_basic" else default)
        for name in ("dataset", "market", "frequency", "adjustment"):
            _text(getattr(self, name), name)
        for name in ("symbols", "fields"):
            object.__setattr__(self, name, _strings(getattr(self, name), name))
        for name in ("start", "end"):
            object.__setattr__(self, name, _day(getattr(self, name)))
        if bool(self.start) != bool(self.end) or (self.start and self.start > self.end):
            raise ValueError("start and end must form an ordered date range")
        object.__setattr__(self, "as_of", _instant(self.as_of))
        object.__setattr__(self, "value_kind", ValueKind(self.value_kind))
        if type(self.limit) is not int or not 1 <= self.limit <= 5000:
            raise ValueError("limit must be between 1 and 5000")
        if not isinstance(self.allow_partial, bool):
            raise ValueError("allow_partial must be boolean")
        for name, allowed in (("statement", {"all", "income", "balance", "cashflow"}),
                              ("statement_scope", {"consolidated", "parent"}),
                              ("period_basis", {"cumulative", "single_quarter"}),
                              ("revision", {"original", "adjusted", "all"})):
            if getattr(self, name) not in allowed:
                raise ValueError("Invalid financial selection")
        if self.dataset != "financial_statements" and (self.statement, self.statement_scope, self.period_basis, self.revision) != ("all", "consolidated", "cumulative", "original"):
            raise ValueError("Financial selections require financial_statements")
        for name in ("provider", "cursor"):
            if getattr(self, name) is not None:
                _text(getattr(self, name), name)


@dataclass(frozen=True)
class FieldDefinition(JsonModel):
    name: str
    dtype: str
    description: str
    unit: Optional[str] = None
    nullable: bool = False


@dataclass(frozen=True)
class DatasetDefinition(JsonModel):
    name: str
    description: str
    fields: tuple[FieldDefinition, ...]
    primary_key: tuple[str, ...]
    date_field: Optional[str] = None
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True)
class DataCapability(JsonModel):
    provider: str
    dataset: str
    fields: tuple[str, ...]
    markets: tuple[str, ...]
    frequencies: tuple[str, ...] = ("1d",)
    adjustments: tuple[str, ...] = ("raw",)
    value_kinds: tuple[ValueKind, ...] = (ValueKind.ACTUAL,)
    history_start: Optional[date] = None
    history_end: Optional[date] = None
    supports_as_of: bool = False
    supports_pagination: bool = False
    adapter_mode: AdapterMode = AdapterMode.UNKNOWN
    access: AccessStatus = AccessStatus.UNKNOWN
    account_scope: str = "default"
    verified_at: Optional[datetime] = None
    generated: bool = False
    coverage_notes: tuple[str, ...] = ()

    def __post_init__(self):
        for name in ("provider", "dataset", "account_scope"):
            _text(getattr(self, name), name)
        for name in ("fields", "markets", "frequencies", "adjustments", "coverage_notes"):
            object.__setattr__(self, name, _strings(getattr(self, name), name))
        object.__setattr__(self, "value_kinds", tuple(ValueKind(v) for v in self.value_kinds))
        for name in ("history_start", "history_end"):
            object.__setattr__(self, name, _day(getattr(self, name)))
        if self.history_start and self.history_end and self.history_start > self.history_end:
            raise ValueError("Invalid history coverage range")
        object.__setattr__(self, "verified_at", _instant(self.verified_at))
        object.__setattr__(self, "adapter_mode", AdapterMode(self.adapter_mode))
        object.__setattr__(self, "access", AccessStatus(self.access))
        for name in ("supports_as_of", "supports_pagination", "generated"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be boolean")


@dataclass
class DataPage(JsonModel):
    """One provider page. Completeness must be asserted, never inferred from row count."""
    records: list[dict[str, Any]]
    provenance: Provenance
    complete: bool = False
    next_cursor: Optional[str] = None
    diagnostics: list[Diagnostic] = field(default_factory=list)


@dataclass
class DataResult(JsonModel):
    request: DataRequest
    request_id: str
    status: Status = Status.UNAVAILABLE
    records: list[dict[str, Any]] = field(default_factory=list)
    definition: Optional[DatasetDefinition] = None
    provenance: Optional[Provenance] = None
    complete: bool = False
    next_cursor: Optional[str] = None
    diagnostics: list[Diagnostic] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True)
class AnnouncementRequest(JsonModel):
    symbols: tuple[str, ...]
    start: date
    end: date
    query: str = ""
    limit: int = 50
    cursor: Optional[str] = None

    def __post_init__(self):
        object.__setattr__(self, "symbols", _strings(self.symbols, "symbols"))
        if not 1 <= len(self.symbols) <= 20 or any(not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", s) for s in self.symbols):
            raise ValueError("Provide 1 to 20 normalized A-share symbols")
        for name in ("start", "end"):
            object.__setattr__(self, name, _day(getattr(self, name)))
        if self.start is None or self.end is None or self.start > self.end or self.end == date.max:
            raise ValueError("Provide an ordered announcement date range")
        if not isinstance(self.query, str) or len(self.query) > 200:
            raise ValueError("query must be a title phrase of at most 200 characters")
        if type(self.limit) is not int or not 1 <= self.limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if self.cursor is not None:
            _text(self.cursor, "cursor")


@dataclass(frozen=True)
class MaterialRequest(JsonModel):
    question: str
    urls: tuple[str, ...]
    max_chars: int = 20000
    max_spans: int = 10
    web_read_mode: str = "auto"
    follow_links: int = 0
    link_domains: tuple[str, ...] = ()
    previous_text_hashes: dict[str, str] = field(default_factory=dict)
    wechat_cache_mode: str = "use"
    archive_dir: Optional[str] = None
    archive_images: bool = False
    max_archive_images: int = 10
    video_languages: tuple[str, ...] = ('zh-Hans', 'zh-CN', 'zh', 'en')
    audio_mode: str = 'metadata'
    audio_start_seconds: int = 0
    audio_max_seconds: int = 60
    audio_window_count: int = 1
    xhs_comment_limit: int = 0
    xhs_cache_mode: str = 'use'

    def __post_init__(self):
        _text(self.question, "question")
        if (type(self.xhs_comment_limit) is not int or not 0<=self.xhs_comment_limit<=19
                or self.xhs_cache_mode not in {'use','refresh'}):
            raise ValueError('Invalid XHS read options')
        if self.audio_mode not in {'metadata', 'transcribe', 'cache_only'}:
            raise ValueError('Invalid audio_mode')
        if (type(self.audio_start_seconds) is not int or not 0 <= self.audio_start_seconds <= 604800
                or type(self.audio_max_seconds) is not int or not 1 <= self.audio_max_seconds <= 180
                or type(self.audio_window_count) is not int or not 1 <= self.audio_window_count <= 5):
            raise ValueError('Invalid audio window')
        from .materials import _video_languages
        object.__setattr__(self, 'video_languages', _video_languages(self.video_languages))
        if self.archive_dir is not None and (not isinstance(self.archive_dir, str) or not self.archive_dir.strip()
                or len(self.archive_dir) > 4096 or any(ord(c) < 32 for c in self.archive_dir)):
            raise ValueError("Invalid archive directory")
        if type(self.archive_images) is not bool or self.archive_images and not self.archive_dir:
            raise ValueError("Image downloads require an explicit archive directory")
        if type(self.max_archive_images) is not int or not 0 <= self.max_archive_images <= 20:
            raise ValueError("Image budget must be between 0 and 20")
        if self.wechat_cache_mode not in ("use", "refresh", "off"):
            raise ValueError("Invalid WeChat cache mode")
        from ir_search.infrastructure.web_toolkit import WEB_READ_MODES
        if self.web_read_mode not in WEB_READ_MODES:
            raise ValueError("Invalid web read mode")
        if type(self.follow_links) is not int or not 0 <= self.follow_links <= 10:
            raise ValueError("follow_links must be between 0 and 10")
        domains = _strings(self.link_domains, "link_domains")
        if len(domains) > 10 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", d) or "." not in d for d in domains):
            raise ValueError("Invalid link domains")
        object.__setattr__(self, "link_domains", domains)
        if self.follow_links and not domains:
            raise ValueError("Explicit link_domains required to follow links")
        if not isinstance(self.previous_text_hashes, dict) or len(self.previous_text_hashes) > 20:
            raise ValueError("Invalid previous text hashes")
        from .materials import _reference
        for url, digest in self.previous_text_hashes.items():
            _reference(url, web_only=True)
            if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
                raise ValueError("Expected SHA-256 text hashes")
        object.__setattr__(self, "previous_text_hashes", dict(self.previous_text_hashes))
        object.__setattr__(self, "urls", _strings(self.urls, "urls"))
        if not 1 <= len(self.urls) <= 10:
            raise ValueError("Provide 1 to 10 URLs from search or another explicit source")
        if type(self.max_chars) is not int or not 1 <= self.max_chars <= 100000:
            raise ValueError("max_chars must be between 1 and 100000")
        if type(self.max_spans) is not int or not 1 <= self.max_spans <= 50:
            raise ValueError("max_spans must be between 1 and 50")


@dataclass
class Material(JsonModel):
    doc_id: str
    url: str
    title: str
    text: str
    provenance: Provenance
    published_at: Optional[datetime]
    extraction_method: str
    text_origin: str
    evidence_spans: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source_text_trust: str = "untrusted"
    original_url: Optional[str] = None
    sections: list[dict[str, Any]] = field(default_factory=list)
    attachments: list[dict[str, Any]] = field(default_factory=list)
    text_provider: Optional[str] = None
    text_hash: Optional[str] = None
    links: list[dict[str, Any]] = field(default_factory=list)
    read_details: dict[str, Any] = field(default_factory=dict)
    article: dict[str, Any] = field(default_factory=dict)
    archive: dict[str, Any] = field(default_factory=dict)


@dataclass
class MaterialBundle(JsonModel):
    request: MaterialRequest
    request_id: str
    status: Status = Status.UNAVAILABLE
    materials: list[Material] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION
    source_text_trust: str = "untrusted"
    reads: list[dict[str, Any]] = field(default_factory=list)
    discovery: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AdvisoryResult(JsonModel):
    """Reserved output boundary only; no advisory execution is enabled."""
    provider: str
    answer: str
    generated_at: datetime
    generated: bool = field(default=True, init=False)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        _text(self.provider, "provider")
        object.__setattr__(self, "generated_at", _instant(self.generated_at))
        if self.generated_at is None:
            raise ValueError("generated_at is required")
