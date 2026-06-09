from __future__ import annotations

import re
from datetime import datetime, timezone

from .config import load_yaml
from .models import EvidenceType, Hit, Intent, Query, SourceTier


EVIDENCE_SCORES_BY_INTENT: dict[Intent, dict[EvidenceType, float]] = {
    Intent.FILING: {
        EvidenceType.ANNOUNCEMENT: 1.0,
        EvidenceType.FINANCIAL_REPORT: 1.0,
        EvidenceType.POLICY_DOC: 0.8,
        EvidenceType.NEWS: 0.45,
        EvidenceType.SOCIAL_POST: 0.1,
    },
    Intent.EARNINGS: {
        EvidenceType.FINANCIAL_REPORT: 1.0,
        EvidenceType.EARNINGS_CALL: 0.9,
        EvidenceType.ANNOUNCEMENT: 0.85,
        EvidenceType.BROKER_REPORT: 0.7,
        EvidenceType.NEWS: 0.45,
    },
    Intent.BROKER_RESEARCH: {
        EvidenceType.BROKER_REPORT: 1.0,
        EvidenceType.OPINION: 0.75,
        EvidenceType.NEWS: 0.5,
        EvidenceType.SOCIAL_POST: 0.15,
    },
    Intent.POLICY: {
        EvidenceType.POLICY_DOC: 1.0,
        EvidenceType.NEWS: 0.55,
        EvidenceType.OPINION: 0.35,
        EvidenceType.SOCIAL_POST: 0.1,
    },
}


DEFAULT_EVIDENCE_SCORES = {
    EvidenceType.FINANCIAL_REPORT: 0.9,
    EvidenceType.ANNOUNCEMENT: 0.85,
    EvidenceType.POLICY_DOC: 0.85,
    EvidenceType.BROKER_REPORT: 0.75,
    EvidenceType.EARNINGS_CALL: 0.7,
    EvidenceType.DATA_TABLE: 0.65,
    EvidenceType.NEWS: 0.5,
    EvidenceType.OPINION: 0.45,
    EvidenceType.SOCIAL_POST: 0.15,
    EvidenceType.UNKNOWN: 0.25,
}


def rerank(q: Query, hits: list[Hit]) -> list[Hit]:
    source_counts = _source_agreement_counts(hits)
    weights = _weights(q.intent)
    for hit in hits:
        parts = {
            "authority": authority_score(hit),
            "relevance": relevance_score(q, hit),
            "evidence_type": evidence_type_score(q.intent, hit),
            "freshness": freshness_score(hit),
            "agreement": agreement_score(hit, source_counts),
        }
        score = sum(parts[name] * weight for name, weight in weights.items())
        score -= noise_penalty(hit)
        hit.rank_score = round(max(0.0, min(1.0, score)), 4)
        hit.extra["rank_parts"] = parts
    return sorted(hits, key=lambda h: h.rank_score, reverse=True)


def authority_score(hit: Hit) -> float:
    return {
        SourceTier.UGC: 0.1,
        SourceTier.MEDIA: 0.4,
        SourceTier.BROKER: 0.65,
        SourceTier.COMPANY: 0.75,
        SourceTier.EXCHANGE_FILING: 0.92,
        SourceTier.REGULATOR: 1.0,
    }[hit.tier]


def relevance_score(q: Query, hit: Hit) -> float:
    text = f"{hit.title} {hit.snippet}".lower()
    terms = extract_query_terms(q)
    matched = [term for term in terms if term and term.lower() in text]
    if matched:
        return min(1.0, 0.35 + len(set(matched)) / max(2, len(set(terms))))
    tokens = [token for token in q.text.lower().split() if len(token) > 1]
    if not tokens:
        return 0.2
    return sum(1 for token in tokens if token in text) / len(tokens)


STOP_TERMS = {
    "最新",
    "情况",
    "影响",
    "如何",
    "怎么看",
    "分析",
    "解读",
    "观点",
    "相关",
    "the",
    "and",
    "for",
}


def extract_query_terms(q: Query) -> list[str]:
    terms: list[str] = []
    for entity in q.entities:
        terms.extend(entity.names)
        terms.extend(entity.aliases)
        terms.extend(entity.codes)
    terms.extend(q.expanded_terms)
    terms.extend(_split_query_text(q.text))
    return _dedupe_terms([term for term in terms if _is_relevance_term(term)])


def _split_query_text(text: str) -> list[str]:
    return [part for part in re.split(r"[\s,，。；;：:/\\|()（）]+", text) if part]


def _is_relevance_term(term: str) -> bool:
    stripped = term.strip()
    if not stripped:
        return False
    if stripped in STOP_TERMS or stripped.lower() in STOP_TERMS:
        return False
    if stripped.isdigit():
        return len(stripped) >= 4
    if any("\u4e00" <= ch <= "\u9fff" for ch in stripped):
        return len(stripped) >= 2
    return len(stripped) >= 3


def _dedupe_terms(terms: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for term in terms:
        key = term.lower()
        if key not in seen:
            seen.add(key)
            out.append(term)
    return out


def evidence_type_score(intent: Intent, hit: Hit) -> float:
    return EVIDENCE_SCORES_BY_INTENT.get(intent, DEFAULT_EVIDENCE_SCORES).get(
        hit.evidence_type, DEFAULT_EVIDENCE_SCORES[hit.evidence_type]
    )


def freshness_score(hit: Hit) -> float:
    if not hit.published_at:
        return 0.35
    now = datetime.now(timezone.utc)
    published = hit.published_at
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    days = max(0, (now - published).days)

    if hit.evidence_type in {
        EvidenceType.NEWS,
        EvidenceType.BROKER_REPORT,
        EvidenceType.SOCIAL_POST,
        EvidenceType.OPINION,
    }:
        return _decay(days, half_life=30)
    if hit.evidence_type in {EvidenceType.POLICY_DOC, EvidenceType.DATA_TABLE}:
        return _decay(days, half_life=180)
    if hit.evidence_type in {EvidenceType.ANNOUNCEMENT, EvidenceType.FINANCIAL_REPORT}:
        return max(0.35, _decay(days, half_life=120))
    return _decay(days, half_life=90)


def agreement_score(hit: Hit, counts: dict[str, int]) -> float:
    if not hit.canonical_url:
        return 0.0
    return min(1.0, max(0, counts.get(hit.canonical_url, 1) - 1) / 3)


def noise_penalty(hit: Hit) -> float:
    text = f"{hit.title} {hit.snippet}".lower()
    penalty = 0.0
    if hit.tier == SourceTier.UGC:
        penalty += 0.06
    if any(word in text for word in ["广告", "开户", "课程", "推广"]):
        penalty += 0.12
    return penalty


def _weights(intent: Intent) -> dict[str, float]:
    raw = load_yaml("rerank_weights.yaml")
    weights = raw.get(intent.name, raw["default"])
    total = sum(weights.values())
    return {name: value / total for name, value in weights.items()}


def _source_agreement_counts(hits: list[Hit]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for hit in hits:
        if hit.canonical_url:
            counts[hit.canonical_url] = counts.get(hit.canonical_url, 0) + len(set(hit.found_by))
    return counts


def _decay(days: int, half_life: int) -> float:
    return 0.5 ** (days / half_life)
