from __future__ import annotations

import hashlib
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
            confidence = min(0.92, supporting[0][0])
        elif contradiction_spans:
            status = "contradicted"
            confidence = min(0.88, contradicting[0][0])
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
    claim_negative = _has_any(claim, NEGATIVE_TERMS)
    evidence_negative = _has_any(evidence_text, NEGATIVE_TERMS)
    claim_positive = _has_any(claim, POSITIVE_TERMS)
    evidence_positive = _has_any(evidence_text, POSITIVE_TERMS)
    if claim_positive and evidence_negative:
        return True
    if claim_negative and evidence_positive:
        return True
    return _has_any(evidence_text, EXPLICIT_REFUTATION_TERMS)


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


NEGATIVE_TERMS = {
    "未",
    "不",
    "无",
    "否认",
    "下滑",
    "下降",
    "减少",
    "不及",
    "亏损",
    "风险",
    "not",
    "decline",
    "decrease",
    "weak",
    "risk",
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

EXPLICIT_REFUTATION_TERMS = {
    "不属实",
    "未证实",
    "没有证据",
    "not confirmed",
    "no evidence",
    "denied",
}
