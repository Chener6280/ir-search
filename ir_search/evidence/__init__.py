from __future__ import annotations

from urllib.parse import urlparse

from ir_search.config import load_yaml
from ir_search.models import EvidenceType, Hit, SourceTier

from .extractor import extract_evidence, freshness_bucket
from .models import ClaimVerification, EvidenceSpan
from .verifier import verify_claims


def classify_hit(hit: Hit) -> Hit:
    hit.tier = classify_tier(hit.url, hit.source)
    hit.evidence_type = classify_evidence_type(hit)
    return hit


def classify_tier(url: str, source: str = "") -> SourceTier:
    domain = _domain(url)
    rules = load_yaml("source_tiers.yaml").get("source_tiers", {})
    for needle, tier_name in rules.items():
        if needle in domain:
            return SourceTier[tier_name]

    source_map = {
        "cninfo": SourceTier.EXCHANGE_FILING,
        "sse": SourceTier.EXCHANGE_FILING,
        "szse": SourceTier.EXCHANGE_FILING,
        "hkex": SourceTier.EXCHANGE_FILING,
        "sec": SourceTier.EXCHANGE_FILING,
        "regulator_sites": SourceTier.REGULATOR,
        "broker_research": SourceTier.BROKER,
        "company_ir": SourceTier.COMPANY,
        "wechat_opencli": SourceTier.MEDIA,
        "manual_wechat": SourceTier.MEDIA,
        "zsxq": SourceTier.UGC,
        "longbridge": SourceTier.MEDIA,
        "tushare": SourceTier.MEDIA,
        "dajiala": SourceTier.MEDIA,
        "market_public": SourceTier.MEDIA,
    }
    return source_map.get(source, SourceTier.MEDIA)


def classify_evidence_type(hit: Hit) -> EvidenceType:
    if hit.evidence_type != EvidenceType.UNKNOWN:
        return hit.evidence_type
    if hit.extra.get("evidence_type") == "search_result" or hit.extra.get("coverage_status") == "partial":
        return EvidenceType.UNKNOWN

    domain = _domain(hit.url)
    text = f"{hit.title} {hit.snippet}".lower()
    patterns = load_yaml("domain_rules.yaml").get("evidence_patterns", {})

    order = [
        EvidenceType.FINANCIAL_REPORT,
        EvidenceType.ANNOUNCEMENT,
        EvidenceType.BROKER_REPORT,
        EvidenceType.POLICY_DOC,
        EvidenceType.EARNINGS_CALL,
        EvidenceType.DATA_TABLE,
        EvidenceType.SOCIAL_POST,
        EvidenceType.OPINION,
    ]
    for evidence_type in order:
        rule = patterns.get(evidence_type.name, {})
        if _any_contains(text, rule.get("title_contains", [])):
            return evidence_type
        if _any_contains(domain, rule.get("domain_contains", [])):
            return evidence_type

    if hit.tier == SourceTier.UGC:
        return EvidenceType.SOCIAL_POST
    if hit.tier in (SourceTier.REGULATOR, SourceTier.EXCHANGE_FILING):
        return EvidenceType.ANNOUNCEMENT
    return EvidenceType.NEWS


def _domain(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _any_contains(text: str, needles: list[str]) -> bool:
    return any(needle.lower() in text for needle in needles)


__all__ = [
    "ClaimVerification",
    "EvidenceSpan",
    "classify_evidence_type",
    "classify_hit",
    "classify_tier",
    "extract_evidence",
    "freshness_bucket",
    "verify_claims",
]
