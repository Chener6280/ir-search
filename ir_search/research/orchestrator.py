from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Callable, Optional

from ir_search.documents import document_from_hit, fetch_document
from ir_search.documents.models import Document
from ir_search.evidence import extract_evidence, verify_claims
from ir_search.evidence.models import ClaimVerification, EvidenceSpan
from ir_search.kernel import search as default_search
from ir_search.models import FallbackPolicy, Hit, Intent, Query, SearchResult, SourceTier, TimeWindow

from .planner import plan_research_queries
from .schemas import ResearchRun
from .synthesizer import synthesize_answer


def deep_research(
    question: str,
    *,
    intent: str = "auto",
    freshness: str = "30d",
    max_rounds: int = 3,
    max_searches: int = 8,
    max_documents: int = 12,
    allow_media: bool = True,
    allow_wechat: bool = True,
    allow_broker: bool = True,
    required_source_tiers: Optional[list[str]] = None,
    language: str = "zh",
    output_style: str = "finance_memo",
    search_fn: Callable[[Query], SearchResult] = default_search,
) -> ResearchRun:
    """Run bounded search, fetch, evidence extraction, and claim verification."""

    del language, output_style
    started = datetime.now(timezone.utc)
    run_id = _run_id(question, started)
    max_rounds = max(1, min(max_rounds, 3))
    max_searches = max(1, min(max_searches, 8))
    max_documents = max(1, min(max_documents, 12))
    plan = plan_research_queries(
        question,
        intent=intent,
        max_searches=max_searches,
        allow_media=allow_media,
        allow_wechat=allow_wechat,
        allow_broker=allow_broker,
    )

    search_log: list[dict] = []
    diagnostics: list[dict] = []
    hits_by_url: dict[str, Hit] = {}
    for query_text in plan.queries[:max_searches]:
        q = Query(
            text=query_text,
            count=max_documents,
            window=TimeWindow(raw=freshness),
            intent=_intent_from_string(plan.intent),
            allow_fallback=True,
            fallback_policy=FallbackPolicy.QUOTA_ONLY,
            fallback_on_empty=False,
        )
        result = search_fn(q)
        search_log.append(
            {
                "query": query_text,
                "n_hits": len(result.hits),
                "sources": [status.source for status in result.diagnostics],
            }
        )
        diagnostics.extend(result.to_dict()["diagnostics"])
        for hit in result.hits:
            key = hit.canonical_url or hit.url
            hits_by_url.setdefault(key, hit)
        if len(hits_by_url) >= max_documents:
            break

    selected_hits = list(hits_by_url.values())[:max_documents]
    documents = [fetch_document_for_hit(hit) for hit in selected_hits]
    evidence_spans: list[EvidenceSpan] = []
    for document in documents:
        evidence_spans.extend(extract_evidence(document, question, max_spans=5))
    evidence_spans.sort(key=lambda span: span.relevance_score, reverse=True)
    evidence_spans = evidence_spans[: max_documents * 3]

    claims = draft_claim_candidates(question, evidence_spans)
    tiers = [_tier_from_string(item) for item in required_source_tiers or []]
    claim_ledger = verify_claims(
        claims,
        evidence_spans=evidence_spans,
        max_rounds=max_rounds - 1,
        required_source_tiers=[tier for tier in tiers if tier is not None] or None,
    )
    source_matrix = build_source_matrix(claim_ledger)
    unverified_items = build_unverified_items(claim_ledger, diagnostics, documents)
    answer = synthesize_answer(
        run_id=run_id,
        question=question,
        search_log=search_log,
        evidence_spans=evidence_spans,
        claim_ledger=claim_ledger,
        source_matrix=source_matrix,
        diagnostics=diagnostics,
        unverified_items=unverified_items,
    )
    return ResearchRun(
        run_id=run_id,
        question=question,
        started_at=started,
        finished_at=datetime.now(timezone.utc),
        search_log=search_log,
        documents_read=documents,
        evidence_spans=evidence_spans,
        claim_ledger=claim_ledger,
        source_matrix=source_matrix,
        answer=answer,
        diagnostics=diagnostics,
        unverified_items=unverified_items,
        extra={
            "max_rounds": max_rounds,
            "max_searches": max_searches,
            "max_documents": max_documents,
            "source_text_trust": "untrusted",
        },
    )


def fetch_document_for_hit(hit: Hit) -> Document:
    if hit.extra.get("adapter_mode") == "mock" or hit.source == "manual_wechat" or hit.extra.get("content"):
        return document_from_hit(hit)
    fetched = fetch_document(hit.url, source_hint=hit.source, max_chars=20000)
    if fetched.errors or not fetched.text.strip():
        return document_from_hit(hit, fetch_errors=fetched.errors or fetched.warnings)
    fetched.title = fetched.title or hit.title
    fetched.source = hit.source
    fetched.source_tier = hit.tier
    fetched.evidence_type = hit.evidence_type
    fetched.published_at = fetched.published_at or hit.published_at
    fetched.extra["adapter_mode"] = hit.extra.get("adapter_mode")
    fetched.extra["is_fallback_result"] = hit.extra.get("is_fallback_result", False)
    return fetched


def draft_claim_candidates(question: str, evidence_spans: list[EvidenceSpan]) -> list[str]:
    claims: list[str] = []
    for span in evidence_spans[:3]:
        sentence = _first_sentence(span.text)
        if sentence and sentence not in claims:
            claims.append(sentence)
    if not claims:
        claims.append(f"需要验证：{question}")
    return claims[:5]


def build_source_matrix(claim_ledger: list[ClaimVerification]) -> list[dict]:
    rows: list[dict] = []
    for entry in claim_ledger:
        row = {
            "claim_id": entry.claim_id,
            "claim": entry.claim,
            "official_filing": "missing",
            "company_ir": "missing",
            "regulator": "missing",
            "broker": "missing",
            "media": "missing",
            "wechat": "missing",
            "final_status": entry.status,
        }
        for span in entry.supporting_spans:
            row[_matrix_column(span)] = "support"
        for span in entry.contradicting_spans:
            row[_matrix_column(span)] = "contradict"
        rows.append(row)
    return rows


def build_unverified_items(claim_ledger: list[ClaimVerification], diagnostics: list[dict], documents: list[Document]) -> list[str]:
    items = [entry.claim for entry in claim_ledger if entry.status == "insufficient_evidence"]
    failed_sources = [item.get("source") for item in diagnostics if not item.get("ok")]
    if failed_sources:
        items.append(f"部分来源失败或不可用：{', '.join(sorted(set(filter(None, failed_sources))))}")
    snippet_docs = [document.title for document in documents if document.content_type == "snippet"]
    if snippet_docs:
        items.append("部分文档仅使用搜索摘要，尚未读取全文")
    return items


def _matrix_column(span: EvidenceSpan) -> str:
    if span.source in {"manual_wechat", "wechat_opencli", "dajiala"} or "mp.weixin.qq.com" in span.url:
        return "wechat"
    if span.source_tier == SourceTier.EXCHANGE_FILING:
        return "official_filing"
    if span.source_tier == SourceTier.COMPANY:
        return "company_ir"
    if span.source_tier == SourceTier.REGULATOR:
        return "regulator"
    if span.source_tier == SourceTier.BROKER:
        return "broker"
    return "media"


def _intent_from_string(value: Optional[str]) -> Intent:
    if not value:
        return Intent.GENERAL
    try:
        return Intent(value.lower())
    except ValueError:
        if value.upper() in Intent.__members__:
            return Intent[value.upper()]
        return Intent.GENERAL


def _tier_from_string(value: str) -> Optional[SourceTier]:
    if not value:
        return None
    key = value.upper()
    if key in SourceTier.__members__:
        return SourceTier[key]
    try:
        return SourceTier(int(value))
    except (TypeError, ValueError):
        return None


def _first_sentence(text: str) -> str:
    for sep in ["。", "\n", ".", "；", ";"]:
        if sep in text:
            return text.split(sep, 1)[0].strip()[:220]
    return text.strip()[:220]


def _run_id(question: str, started: datetime) -> str:
    digest = hashlib.sha1(f"{question}|{started.isoformat()}".encode("utf-8")).hexdigest()[:10]
    return f"dr_{started.strftime('%Y%m%d_%H%M%S')}_{digest}"
