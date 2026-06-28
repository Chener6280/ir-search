from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum, IntEnum
from typing import Any, Iterable, Optional, Union


class Lang(str, Enum):
    ZH = "zh"
    EN = "en"
    MIXED = "mixed"
    AUTO = "auto"


class Intent(str, Enum):
    GENERAL = "general"
    COMPANY_NEWS = "company_news"
    FILING = "filing"
    EARNINGS = "earnings"
    BROKER_RESEARCH = "broker_research"
    POLICY = "policy"
    MACRO = "macro"
    INDUSTRY_CHAIN = "industry_chain"
    PRICE_SUPPLY_DEMAND = "price_supply_demand"
    COMPETITOR = "competitor"
    SENTIMENT = "sentiment"
    OVERSEAS_MAPPING = "overseas_mapping"


class SourceTier(IntEnum):
    UGC = 1
    MEDIA = 2
    BROKER = 3
    COMPANY = 4
    EXCHANGE_FILING = 5
    REGULATOR = 6


class ResultKind(str, Enum):
    FILING_DOCUMENT = "filing_document"
    ANNOUNCEMENT = "announcement"
    WEB_DOCUMENT = "web_document"
    NEWS = "news"
    POLICY_DOC = "policy_doc"
    DISCOVERY_URL = "discovery_url"
    MARKET_QUOTE = "market_quote"
    FINANCIAL_TABLE = "financial_table"
    TIMESERIES = "timeseries"
    RATING_SNAPSHOT = "rating_snapshot"
    PUBLIC_SNAPSHOT = "public_snapshot"
    SOCIAL_POST = "social_post"
    OPINION = "opinion"
    UNKNOWN = "unknown"


class SourceAuthority(str, Enum):
    OFFICIAL_FILING = "official_filing"
    REGULATOR = "regulator"
    COMPANY = "company"
    BROKER_RESEARCH = "broker_research"
    COMMERCIAL_SEARCH = "commercial_search"
    DISCOVERY = "discovery"
    ANONYMOUS_SEARCH = "anonymous_search"
    DATA_VENDOR = "data_vendor"
    BROKER_PLATFORM = "broker_platform"
    PUBLIC_MARKET_DATA = "public_market_data"
    MEDIA = "media"
    UGC = "ugc"
    UNKNOWN = "unknown"


class CoverageStatus(str, Enum):
    COVERED = "covered"
    PARTIAL_DISCOVERY = "partial_discovery"
    PARTIAL_DATA = "partial_data"
    TRUE_NEGATIVE = "true_negative"
    BLOCKED_BY_POLICY = "blocked_by_policy"
    FAILED = "failed"
    UNKNOWN = "unknown"


class FailureKind(str, Enum):
    NONE = "none"
    NO_CREDENTIAL = "no_credential"
    QUOTA = "quota"
    RATE_LIMIT = "rate_limit"
    NETWORK = "network"
    TIMEOUT = "timeout"
    UPSTREAM_SCHEMA = "upstream_schema"
    UNIMPLEMENTED = "unimplemented"
    BLOCKED_BY_POLICY = "blocked_by_policy"
    UNKNOWN = "unknown"


class EvidenceType(str, Enum):
    ANNOUNCEMENT = "announcement"
    FINANCIAL_REPORT = "financial_report"
    EARNINGS_CALL = "earnings_call"
    BROKER_REPORT = "broker_report"
    NEWS = "news"
    POLICY_DOC = "policy_doc"
    SOCIAL_POST = "social_post"
    DATA_TABLE = "data_table"
    OPINION = "opinion"
    UNKNOWN = "unknown"


class EntityType(str, Enum):
    COMPANY = "company"
    SECURITY = "security"
    PRODUCT = "product"
    INDUSTRY = "industry"
    PERSON = "person"
    INSTITUTION = "institution"
    POLICY = "policy"


class FallbackPolicy(str, Enum):
    NONE = "none"
    QUOTA_ONLY = "quota_only"
    NETWORK_ONLY = "network_only"
    ALL = "all"


@dataclass
class Entity:
    canonical_id: str
    entity_type: EntityType
    names: list[str]
    aliases: list[str]
    codes: list[str]
    market: Optional[str] = None
    related_terms: list[str] = field(default_factory=list)


@dataclass
class TimeWindow:
    raw: str = "noLimit"
    start: Optional[datetime] = None
    end: Optional[datetime] = None


@dataclass
class Query:
    """Search request options.

    fallback_on_empty only triggers configured fallback routes when
    fallback_policy is ALL; QUOTA_ONLY and NETWORK_ONLY apply to errors only.
    """

    text: str
    intent: Intent = Intent.GENERAL
    lang: Lang = Lang.AUTO
    window: TimeWindow = field(default_factory=TimeWindow)
    sources: Optional[list[str]] = None
    count: int = 10
    entities: list[Entity] = field(default_factory=list)
    expanded_terms: list[str] = field(default_factory=list)
    secondary_intents: list[Intent] = field(default_factory=list)
    intent_scores: dict[str, float] = field(default_factory=dict)
    allow_browser_fallback: bool = False
    allow_fallback: bool = False
    fallback_policy: FallbackPolicy = FallbackPolicy.NONE
    # Empty-result fallback is intentionally limited to fallback_policy=ALL.
    fallback_on_empty: bool = False
    # Reserved flag; LLM query rewriting must stay outside the deterministic search hot path.
    llm_rewrite: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.fallback_policy, str):
            self.fallback_policy = FallbackPolicy(self.fallback_policy)

    @classmethod
    def of(cls, x: Union[str, "Query"]) -> "Query":
        if isinstance(x, Query):
            return x
        return cls(text=str(x))


@dataclass
class Hit:
    title: str
    url: str
    snippet: str
    source: str
    tier: SourceTier = SourceTier.MEDIA
    evidence_type: EvidenceType = EvidenceType.UNKNOWN
    published_at: Optional[datetime] = None
    fetched_at: Optional[datetime] = None
    raw_score: Optional[float] = None
    found_by: list[str] = field(default_factory=list)
    rank_score: float = 0.0
    canonical_url: str = ""
    matched_entities: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.found_by:
            self.found_by = [self.source]


@dataclass
class SourceStatus:
    source: str
    ok: bool
    n_results: int
    error: Optional[str]
    elapsed_ms: int
    cache_hit: Optional[bool] = None
    adapter_mode: str = "unknown"
    result_kinds: list[ResultKind] = field(default_factory=list)
    authority: SourceAuthority = SourceAuthority.UNKNOWN
    coverage_status: CoverageStatus = CoverageStatus.UNKNOWN
    failure_kind: FailureKind = FailureKind.NONE
    fallback_parent: Optional[str] = None
    fallback_hop: int = 0
    skipped: bool = False
    skipped_reason: Optional[str] = None


@dataclass
class SearchResult:
    query: Query
    hits: list[Hit]
    diagnostics: list[SourceStatus]

    def __iter__(self) -> Iterable[Hit]:
        return iter(self.hits)

    def __len__(self) -> int:
        return len(self.hits)

    @property
    def failed_sources(self) -> list[str]:
        return [s.source for s in self.diagnostics if not s.ok]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["query"]["intent"] = self.query.intent.value
        data["query"]["lang"] = self.query.lang.value
        data["query"]["fallback_policy"] = self.query.fallback_policy.value
        data["query"]["secondary_intents"] = [intent.value for intent in self.query.secondary_intents]
        for entity in data["query"]["entities"]:
            entity["entity_type"] = entity["entity_type"].value
        for hit, hit_data in zip(self.hits, data["hits"]):
            hit_data["tier"] = hit.tier.name
            hit_data["evidence_type"] = hit.evidence_type.value
            hit_data["published_at"] = hit.published_at.isoformat() if hit.published_at else None
            hit_data["fetched_at"] = hit.fetched_at.isoformat() if hit.fetched_at else None
        for status, status_data in zip(self.diagnostics, data["diagnostics"]):
            status_data["result_kinds"] = [kind.value for kind in status.result_kinds]
            status_data["authority"] = status.authority.value
            status_data["coverage_status"] = status.coverage_status.value
            status_data["failure_kind"] = status.failure_kind.value
        return data
