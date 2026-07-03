from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional

from .config import load_yaml
from .models import CoverageStatus, Intent, Query, ResultKind, SourceAuthority


@dataclass(frozen=True)
class SourceCapability:
    source: str
    authority: SourceAuthority = SourceAuthority.UNKNOWN
    result_kinds: list[ResultKind] = field(default_factory=lambda: [ResultKind.UNKNOWN])
    can_fallback_to_authorities: list[SourceAuthority] = field(default_factory=list)
    promotable_from_discovery: bool = False
    max_evidence_status: Optional[CoverageStatus] = None


@dataclass(frozen=True)
class FallbackCandidate:
    source: str
    allowed: bool
    reason: str


@lru_cache(maxsize=None)
def source_capabilities() -> dict[str, SourceCapability]:
    rows = load_yaml("source_capabilities.yaml").get("sources", {})
    return {source: _capability_from_row(source, row or {}) for source, row in rows.items()}


def capability_for(source: str) -> SourceCapability:
    return source_capabilities().get(source, SourceCapability(source=source))


def authority_for(source: str) -> SourceAuthority:
    return capability_for(source).authority


def result_kinds_for(source: str) -> list[ResultKind]:
    return capability_for(source).result_kinds


def fallback_candidates_for(q: Query, from_source: str, raw_sources: list[str]) -> list[FallbackCandidate]:
    return [
        FallbackCandidate(source=target, allowed=allowed, reason=reason)
        for target in raw_sources
        for allowed, reason in [is_fallback_allowed(q, from_source, target)]
    ]


def is_fallback_allowed(q: Query, from_source: str, to_source: str) -> tuple[bool, str]:
    target_authority = authority_for(to_source)

    if q.intent == Intent.FILING and target_authority not in {
        SourceAuthority.OFFICIAL_FILING,
        SourceAuthority.REGULATOR,
        SourceAuthority.COMPANY,
    }:
        return False, "filing_intent_requires_authoritative_source"

    if query_requires_structured_market_data(q) and target_authority not in {
        SourceAuthority.DATA_VENDOR,
        SourceAuthority.BROKER_PLATFORM,
        SourceAuthority.PUBLIC_MARKET_DATA,
    }:
        return False, "structured_data_requires_data_source"

    if target_authority == SourceAuthority.DISCOVERY and not q.allow_fallback:
        return False, "discovery_source_requires_explicit_fallback"

    from_capability = capability_for(from_source)
    if target_authority not in from_capability.can_fallback_to_authorities:
        return False, "target_authority_not_allowed_by_source_capability"

    return True, "allowed"


def query_requires_structured_market_data(q: Query) -> bool:
    if q.intent == Intent.PRICE_SUPPLY_DEMAND:
        return True
    lower = q.text.lower()
    data_needles = [
        "tushare",
        "a-stock-data",
        "股东人数",
        "股东户数",
        "龙虎榜",
        "限售",
        "解禁",
        "财务指标",
        "业绩预告",
        "业绩快报",
        "日行情",
        "换手率",
        "资金流",
        "主力净流入",
        "市值",
        "pe",
        "pb",
        "ps",
        "roe",
        "roa",
        "eps",
    ]
    return any(needle in lower or needle in q.text for needle in data_needles)


def _capability_from_row(source: str, row: dict) -> SourceCapability:
    return SourceCapability(
        source=source,
        authority=_source_authority(row.get("authority")),
        result_kinds=[_result_kind(item) for item in row.get("result_kinds", [])] or [ResultKind.UNKNOWN],
        can_fallback_to_authorities=[
            _source_authority(item) for item in row.get("can_fallback_to_authorities", [])
        ],
        promotable_from_discovery=bool(row.get("promotable_from_discovery", False)),
        max_evidence_status=_coverage_status(row.get("max_evidence_status")),
    )


def _source_authority(value: object) -> SourceAuthority:
    if isinstance(value, SourceAuthority):
        return value
    try:
        return SourceAuthority(str(value))
    except ValueError:
        return SourceAuthority.UNKNOWN


def _result_kind(value: object) -> ResultKind:
    if isinstance(value, ResultKind):
        return value
    try:
        return ResultKind(str(value))
    except ValueError:
        return ResultKind.UNKNOWN


def _coverage_status(value: object) -> Optional[CoverageStatus]:
    if value is None:
        return None
    if isinstance(value, CoverageStatus):
        return value
    try:
        return CoverageStatus(str(value))
    except ValueError:
        return CoverageStatus.UNKNOWN
