from __future__ import annotations

import hashlib
from urllib.parse import urlparse
from typing import Callable, Optional

from ir_search.models import Query, SearchResult, SourceTier

from .extractor import terms_for_text
from .models import ClaimVerification, EvidenceSpan


def verify_claims(
    claims: list[str],
    *,
    evidence_spans: list[EvidenceSpan],
    search_fn: Optional[Callable[[Query], SearchResult]] = None,
    max_rounds: int = 2,
    required_source_tiers: Optional[list[SourceTier]] = None,
) -> list[ClaimVerification]:
    """Verify claims against extracted evidence spans without using an LLM."""

    del search_fn
    max_rounds = max(0, min(max_rounds, 2))
    required_tiers = set(required_source_tiers or [])
    verifications: list[ClaimVerification] = []
    for idx, claim in enumerate(claims, start=1):
        claim_terms = terms_for_text(claim)
        supporting: list[tuple[float, EvidenceSpan]] = []
        contradicting: list[tuple[float, EvidenceSpan]] = []
        for span in evidence_spans:
            overlap = text_overlap_score(claim_terms, terms_for_text(span.text))
            if overlap <= 0:
                continue
            weighted = min(1.0, overlap + span.source_tier.value * 0.03 + span.relevance_score * 0.25)
            if looks_contradictory(claim, span.text):
                contradicting.append((weighted, span))
            else:
                supporting.append((weighted, span))

        supporting.sort(key=lambda item: item[0], reverse=True)
        contradicting.sort(key=lambda item: item[0], reverse=True)
        support_spans = [span for _, span in supporting[:5]]
        contradiction_spans = [span for _, span in contradicting[:5]]
        caveats: list[str] = []
        if any(has_risk_caveat(span.text) for span in support_spans + contradiction_spans):
            caveats.append("evidence includes risk or uncertainty caveats")
        status = "insufficient_evidence"
        confidence = 0.0

        if support_spans and contradiction_spans:
            status = "mixed"
            confidence = min(0.75, max(supporting[0][0], contradicting[0][0]))
            caveats.append("supporting and contradicting evidence spans were both found")
        elif support_spans:
            max_tier = max(span.source_tier for span in support_spans)
            missing_required = required_tiers and not any(span.source_tier in required_tiers for span in support_spans)
            if any(span.extra.get("adapter_mode") == "mock" for span in support_spans):
                status = "mixed"
                caveats.append("supporting spans came from mock adapter output")
            elif support_spans and all(span.extra.get("content_type") == "snippet" for span in support_spans):
                status = "mixed"
                caveats.append("supporting spans came only from search snippets, not fetched full documents")
            elif max_tier <= SourceTier.BROKER:
                status = "mixed"
                caveats.append("supported only by broker, media, WeChat, social, or other non-primary sources")
            elif missing_required:
                status = "mixed"
                caveats.append("no evidence span matched the required source tiers")
            else:
                status = "supported"
            confidence = confidence_for_evidence(support_spans, contradiction_spans, best_score=supporting[0][0])
        elif contradiction_spans:
            status = "contradicted"
            confidence = min(0.88, confidence_for_evidence([], contradiction_spans, best_score=contradicting[0][0]))
        else:
            caveats.append("no extracted evidence span had meaningful term overlap with this claim")

        verifications.append(
            ClaimVerification(
                claim_id=f"c{idx}_{_stable_claim_hash(claim)}",
                claim=claim,
                status=status,
                confidence=round(confidence, 4),
                supporting_spans=support_spans,
                contradicting_spans=contradiction_spans,
                caveats=caveats,
                verification_queries=verification_queries_for(claim, max_rounds=max_rounds),
            )
        )
    return verifications


def text_overlap_score(left_terms: set[str], right_terms: set[str]) -> float:
    if not left_terms or not right_terms:
        return 0.0
    overlap = left_terms & right_terms
    return len(overlap) / max(3, len(left_terms))


def looks_contradictory(claim: str, evidence_text: str) -> bool:
    if _has_any(evidence_text, EXPLICIT_REFUTATION_TERMS):
        return True
    claim_positive = _has_any(claim, POSITIVE_TERMS)
    evidence_directional_negative = _has_any(evidence_text, DIRECTIONAL_NEGATIVE_TERMS)
    return claim_positive and evidence_directional_negative and shares_metric_or_entity(claim, evidence_text)


def has_risk_caveat(text: str) -> bool:
    return _has_any(text, RISK_CAVEAT_TERMS)


def shares_metric_or_entity(claim: str, evidence_text: str) -> bool:
    claim_lower = claim.lower()
    evidence_lower = evidence_text.lower()
    if any(term in claim_lower and term in evidence_lower for term in SHARED_METRIC_TERMS):
        return True
    claim_terms = _important_overlap_terms(terms_for_text(claim))
    evidence_terms = _important_overlap_terms(terms_for_text(evidence_text))
    return bool(claim_terms & evidence_terms)


def confidence_for_evidence(
    supporting_spans: list[EvidenceSpan],
    contradicting_spans: list[EvidenceSpan],
    *,
    best_score: float,
) -> float:
    spans = supporting_spans or contradicting_spans
    domains = {_domain(span.url) or span.source for span in spans}
    has_official = any(span.source_tier >= SourceTier.EXCHANGE_FILING for span in spans)
    has_company_or_exchange = any(span.source_tier in {SourceTier.COMPANY, SourceTier.EXCHANGE_FILING} for span in spans)
    has_only_snippet = bool(spans) and all(span.extra.get("content_type") == "snippet" for span in spans)
    has_mock = any(span.extra.get("adapter_mode") in {"mock", "placeholder"} for span in spans)
    confidence = min(best_score, 0.72)
    confidence += 0.05 * min(max(len(domains) - 1, 0), 3)
    confidence += 0.06 if has_official else 0.0
    confidence += 0.04 if has_company_or_exchange else 0.0
    confidence -= 0.10 if has_only_snippet else 0.0
    confidence -= 0.15 if has_mock else 0.0
    confidence -= 0.20 if contradicting_spans and supporting_spans else 0.0
    return max(0.0, min(0.92, confidence))


def verification_queries_for(claim: str, *, max_rounds: int) -> list[str]:
    if max_rounds <= 0:
        return []
    return [
        f"{claim} 反驳 否认 风险",
        f"{claim} official announcement not confirmed",
    ][: max_rounds]


def _stable_claim_hash(claim: str) -> str:
    return hashlib.sha1(claim.encode("utf-8")).hexdigest()[:8]


def _has_any(text: str, needles: set[str]) -> bool:
    lower = text.lower()
    return any(needle in lower or needle in text for needle in needles)


def _important_overlap_terms(terms: set[str]) -> set[str]:
    return {
        term
        for term in terms
        if len(term) >= 2
        and term not in POSITIVE_TERMS
        and term not in DIRECTIONAL_NEGATIVE_TERMS
        and term not in RISK_CAVEAT_TERMS
        and term not in WEAK_OVERLAP_TERMS
    }


def _domain(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


DIRECTIONAL_NEGATIVE_TERMS = {
    "下滑",
    "下降",
    "减少",
    "亏损",
    "低于预期",
    "不及预期",
    "decline",
    "decrease",
    "weaker than expected",
    "below expectation",
}

RISK_CAVEAT_TERMS = {
    "风险",
    "不确定性",
    "压力",
    "risk",
    "uncertainty",
    "pressure",
}

EXPLICIT_REFUTATION_TERMS = {
    "不属实",
    "否认",
    "未证实",
    "未经证实",
    "无证据",
    "没有证据",
    "not confirmed",
    "unconfirmed",
    "denied",
    "no evidence",
}

POSITIVE_TERMS = {
    "增长",
    "上升",
    "提升",
    "强劲",
    "改善",
    "增加",
    "验证",
    "超预期",
    "growth",
    "increase",
    "strong",
    "improve",
    "confirmed",
}

SHARED_METRIC_TERMS = {
    "收入",
    "营收",
    "订单",
    "需求",
    "利润",
    "净利润",
    "毛利率",
    "现金流",
    "业绩",
    "产能",
    "价格",
    "成本",
    "销量",
    "revenue",
    "order",
    "demand",
    "profit",
    "margin",
    "cash flow",
}

WEAK_OVERLAP_TERMS = {
    "相关",
    "方面",
    "情况",
    "影响",
    "分析",
    "认为",
    "显示",
    "可能",
    "公司",
    "业务",
    "最新",
    "recent",
    "company",
}
