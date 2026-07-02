from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Callable, Optional

from ir_search.documents import document_from_hit, fetch_document
from ir_search.documents.fetcher import normalize_evidence_type_for_source
from ir_search.documents.models import Document
from ir_search.evidence import extract_evidence, verify_claims
from ir_search.evidence.models import ClaimVerification, EvidenceSpan
from ir_search.kernel import search as default_search
from ir_search.models import FallbackPolicy, Hit, Intent, Query, SearchResult, SourceTier, TimeWindow
from ir_search.source_health import source_health as default_source_health

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
    source_health_fn: Callable[[], dict] = default_source_health,
) -> ResearchRun:
    """Run bounded search, fetch, evidence extraction, and claim verification."""

    started = datetime.now(timezone.utc)
    run_id = _run_id(question, started)
    max_rounds = max(1, min(max_rounds, 3))
    max_searches = max(1, min(max_searches, 8))
    max_documents = max(1, min(max_documents, 12))
    health = source_health_fn()
    plan = plan_research_queries(
        question,
        intent=intent,
        max_searches=max_searches,
        allow_media=allow_media,
        allow_wechat=allow_wechat,
        allow_broker=allow_broker,
        source_health=health,
    )

    search_log: list[dict] = []
    diagnostics: list[dict] = source_health_diagnostics(plan.required_sources, health)
    reserved_parameters = {
        "language": {"value": language, "status": "reserved_not_applied"},
        "output_style": {"value": output_style, "status": "reserved_not_applied"},
    }
    diagnostics.append({"source": "deep_research", "ok": True, "reserved_parameters": reserved_parameters})
    hits_by_url: dict[str, Hit] = {}
    initial_search_budget = max_searches - 1 if plan.required_sources and max_searches > 1 else max_searches
    for query_text in plan.queries[:initial_search_budget]:
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
                "hit_sources": sorted({hit.source for hit in result.hits}),
                "official_only": False,
            }
        )
        diagnostics.extend(result.to_dict()["diagnostics"])
        for hit in result.hits:
            key = hit.canonical_url or hit.url
            hits_by_url.setdefault(key, hit)
        if len(hits_by_url) >= max_documents:
            break

    official_second_pass = maybe_run_official_second_pass(
        question=question,
        freshness=freshness,
        max_documents=max_documents,
        max_searches=max_searches,
        search_log=search_log,
        hits_by_url=hits_by_url,
        required_sources=plan.required_sources,
        intent=plan.intent,
        search_fn=search_fn,
    )

    selected_hits = list(hits_by_url.values())[:max_documents]
    documents = [fetch_document_for_hit(hit) for hit in selected_hits]
    evidence_spans: list[EvidenceSpan] = []
    for document in documents:
        evidence_spans.extend(extract_evidence(document, question, max_spans=5))
    evidence_spans.sort(key=lambda span: span.relevance_score, reverse=True)
    evidence_spans = evidence_spans[: max_documents * 3]

    claim_candidates = draft_claim_candidates(question, plan.intent or intent, evidence_spans)
    claims = [candidate["claim"] for candidate in claim_candidates]
    tiers = [_tier_from_string(item) for item in required_source_tiers or []]
    claim_ledger = verify_claims(
        claims,
        evidence_spans=evidence_spans,
        max_rounds=max_rounds - 1,
        required_source_tiers=[tier for tier in tiers if tier is not None] or None,
    )
    apply_freshness_requirements(question, claim_ledger)
    source_matrix = build_source_matrix(claim_ledger)
    source_capabilities = build_source_capabilities(plan.required_sources, health)
    actual_evidence_by_source = build_actual_evidence_by_source(search_log, documents, evidence_spans, claim_ledger)
    official_source_attempts = build_official_source_attempts(
        plan.required_sources,
        source_capabilities,
        actual_evidence_by_source,
    )
    official_gap_report = build_official_gap_report(
        question,
        plan.required_sources,
        source_capabilities,
        actual_evidence_by_source,
        claim_ledger,
    )
    unverified_items = build_unverified_items(claim_ledger, diagnostics, documents)
    unverified_items.extend(plan.warnings)
    if official_gap_report.get("verdict") == "insufficient_primary_source_evidence":
        unverified_items.append("官方一手证据不足：请按 official_gap_report 的 manual_checklist 继续核验。")
    answer = synthesize_answer(
        run_id=run_id,
        question=question,
        search_log=search_log,
        evidence_spans=evidence_spans,
        claim_ledger=claim_ledger,
        source_matrix=source_matrix,
        diagnostics=diagnostics,
        unverified_items=unverified_items,
        official_gap_report=official_gap_report,
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
            "source_health": health,
            "source_capabilities": source_capabilities,
            "actual_evidence_by_source": actual_evidence_by_source,
            "official_source_attempts": official_source_attempts,
            "official_gap_report": official_gap_report,
            "official_second_pass": official_second_pass,
            "reserved_parameters": reserved_parameters,
            "claim_candidates": claim_candidates,
            "plan_warnings": plan.warnings,
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
    evidence_type, warnings = normalize_evidence_type_for_source(
        source_tier=hit.tier,
        source=hit.source,
        url=fetched.canonical_url or fetched.url or hit.url,
        title=fetched.title or hit.title,
        content_type=fetched.content_type,
        current_evidence_type=hit.evidence_type,
    )
    fetched.evidence_type = evidence_type
    fetched.warnings.extend(warnings)
    fetched.published_at = fetched.published_at or hit.published_at
    fetched.extra["adapter_mode"] = hit.extra.get("adapter_mode")
    fetched.extra["is_fallback_result"] = hit.extra.get("is_fallback_result", False)
    return fetched


def maybe_run_official_second_pass(
    *,
    question: str,
    freshness: str,
    max_documents: int,
    max_searches: int,
    search_log: list[dict],
    hits_by_url: dict[str, Hit],
    required_sources: list[str],
    intent: Optional[str],
    search_fn: Callable[[Query], SearchResult],
) -> dict:
    if not required_sources:
        return {"triggered": False, "reason": "no_required_official_sources"}
    if len(search_log) >= max_searches:
        return {"triggered": False, "reason": "search_budget_exhausted"}
    if any(hit.tier >= SourceTier.COMPANY for hit in hits_by_url.values()):
        return {"triggered": False, "reason": "official_hit_already_present"}

    query_text = f"{question} 官方公告 交易所 监管 披露"
    q = Query(
        text=query_text,
        count=max_documents,
        window=TimeWindow(raw=freshness),
        intent=_intent_from_string(intent),
        sources=required_sources,
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
            "hit_sources": sorted({hit.source for hit in result.hits}),
            "official_only": True,
        }
    )
    for hit in result.hits:
        key = hit.canonical_url or hit.url
        hits_by_url.setdefault(key, hit)
    return {
        "triggered": True,
        "query": query_text,
        "required_sources": required_sources,
        "n_hits": len(result.hits),
    }


def draft_claim_candidates(question: str, intent: str, evidence_spans: list[EvidenceSpan]) -> list[dict]:
    candidates: list[dict] = [
        {"claim": f"需要验证：{question}", "origin": "question", "intent": intent or "auto"}
    ]
    for claim in intent_claim_templates(question, intent):
        candidates.append({"claim": claim, "origin": "template", "intent": intent or "auto"})
    for span in evidence_spans[:3]:
        sentence = _first_sentence(span.text)
        if sentence:
            candidates.append({"claim": sentence, "origin": "evidence", "intent": intent or "auto"})

    deduped: list[dict] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate["claim"] in seen:
            continue
        seen.add(candidate["claim"])
        deduped.append(candidate)
    return deduped[:6]


def intent_claim_templates(question: str, intent: str) -> list[str]:
    if intent == "earnings":
        return [
            f"最新业绩数据是否支持该判断：{question}",
            "收入、订单、毛利率、产品结构是否提供支持证据",
            "官方源是否可用并直接支持该判断",
            "主要风险和替代解释是什么",
        ]
    if intent == "policy":
        return [
            "政策文本是否发生变化",
            "变化影响哪些主体、执行时间和适用范围",
            "与旧口径相比有什么差异",
            "哪些内容仍需官方核验",
        ]
    if intent == "industry_chain":
        return [
            "需求侧是否有支持证据",
            "价格或成本侧是否有支持证据",
            "供给或产能侧是否有支持证据",
            "媒体、券商或微信观点是否被官方或公司源确认",
        ]
    if intent == "wechat_crosscheck":
        return [
            "微信文章只作为候选来源",
            "必须尝试官方、公司或媒体交叉验证",
            "没有一级来源时最终状态不得是 fully supported",
        ]
    return []


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
    authority_unavailable = [
        item.get("source")
        for item in diagnostics
        if item.get("source_health") and item.get("adapter_mode") in {"mock", "placeholder"}
    ]
    if authority_unavailable:
        items.append("部分权威源当前为 mock/placeholder，未能取得真实官方全文。")
    return items


def build_source_capabilities(required_sources: list[str], health: dict) -> dict[str, dict]:
    capabilities: dict[str, dict] = {}
    sources = health.get("sources", {}) if isinstance(health, dict) else {}
    for source in sorted(set(required_sources) | set(sources)):
        status = sources.get(source, {})
        capabilities[source] = {
            "adapter_mode": status.get("adapter_mode", "unknown"),
            "ok": bool(status.get("ok")),
            "notes": list(status.get("notes") or []),
        }
    return capabilities


def build_actual_evidence_by_source(
    search_log: list[dict],
    documents: list[Document],
    evidence_spans: list[EvidenceSpan],
    claim_ledger: list[ClaimVerification],
) -> dict[str, dict]:
    searched_sources = {
        source
        for item in search_log
        for source in (item.get("sources") or []) + (item.get("hit_sources") or [])
        if source
    }
    sources = searched_sources | {document.source for document in documents} | {span.source for span in evidence_spans}
    supporting_claims_by_source: dict[str, set[str]] = {}
    for entry in claim_ledger:
        for span in entry.supporting_spans:
            supporting_claims_by_source.setdefault(span.source, set()).add(entry.claim)

    matrix: dict[str, dict] = {}
    for source in sorted(sources):
        source_documents = [document for document in documents if document.source == source]
        fetched_documents = [
            document
            for document in source_documents
            if document.text.strip()
            and not document.errors
            and document.content_type != "snippet"
            and document.extraction_method != "search_hit_snippet_fallback"
        ]
        source_spans = [span for span in evidence_spans if span.source == source]
        matrix[source] = {
            "searched": source in searched_sources,
            "documents_seen": len(source_documents),
            "fetched_documents": len(fetched_documents),
            "evidence_spans": len(source_spans),
            "supporting_claims": sorted(supporting_claims_by_source.get(source, set())),
        }
    return matrix


def build_official_source_attempts(
    required_sources: list[str],
    source_capabilities: dict[str, dict],
    actual_evidence_by_source: dict[str, dict],
) -> list[dict]:
    attempts: list[dict] = []
    for source in required_sources:
        capability = source_capabilities.get(source, {})
        actual = actual_evidence_by_source.get(source, {})
        fetched_documents = int(actual.get("fetched_documents", 0))
        evidence_spans = int(actual.get("evidence_spans", 0))
        searched = bool(actual.get("searched"))
        if evidence_spans:
            status = "evidence_retrieved"
        elif fetched_documents:
            status = "document_fetched_no_evidence_spans"
        elif searched:
            status = "searched_no_evidence_retrieved"
        elif capability.get("adapter_mode") in {"mock", "placeholder", "unknown"} or not capability.get("ok", False):
            status = "source_unavailable_or_placeholder"
        else:
            status = "not_attempted"
        attempts.append(
            {
                "source": source,
                "capability": capability.get("adapter_mode", "unknown"),
                "searched": searched,
                "fetched_documents": fetched_documents,
                "evidence_spans": evidence_spans,
                "status": status,
            }
        )
    return attempts


def build_official_gap_report(
    question: str,
    required_sources: list[str],
    source_capabilities: dict,
    actual_evidence_by_source: dict,
    claim_ledger: list[ClaimVerification],
) -> dict:
    official_sources_required = required_sources or default_official_sources_for_question(question)
    official_sources_with_evidence = [
        source
        for source in official_sources_required
        if actual_evidence_by_source.get(source, {}).get("evidence_spans", 0) > 0
    ]
    official_supported_claims = [
        entry.claim
        for entry in claim_ledger
        if any(span.source_tier >= SourceTier.COMPANY for span in entry.supporting_spans)
    ]
    if official_sources_with_evidence and official_supported_claims:
        verdict = "primary_source_evidence_present"
    else:
        verdict = "insufficient_primary_source_evidence"
    return {
        "verdict": verdict,
        "official_sources_required": official_sources_required,
        "source_capability": {source: source_capabilities.get(source, {}) for source in official_sources_required},
        "actual_retrieval": {source: actual_evidence_by_source.get(source, {}) for source in official_sources_required},
        "official_sources_with_evidence": official_sources_with_evidence,
        "official_supported_claims": official_supported_claims,
        "manual_checklist": manual_checklist_for_official_gap(question, official_sources_required),
    }


def default_official_sources_for_question(question: str) -> list[str]:
    if any(needle in question for needle in ["财报", "季报", "年报", "公告", "公司", "业绩"]):
        return ["cninfo", "company_ir", "sse", "szse", "hkex", "sec"]
    if any(needle in question for needle in ["政策", "监管", "通知", "办法", "规则"]):
        return ["regulator_sites"]
    return ["cninfo", "company_ir", "sse", "szse", "hkex", "sec"]


def manual_checklist_for_official_gap(question: str, official_sources_required: list[str]) -> list[str]:
    checklist = []
    if any(source in official_sources_required for source in ["cninfo", "sse", "szse", "hkex", "sec"]):
        checklist.extend(["cninfo announcements", "exchange filings"])
    if "company_ir" in official_sources_required:
        checklist.append("company IR")
    if "regulator_sites" in official_sources_required:
        checklist.append("regulator or ministry policy text")
    if any(term in question.lower() for term in ["overseas", "海外", "cloud", "ai"]):
        checklist.append("overseas cloud vendor 10-K / earnings call")
    return checklist or ["official filings", "company IR", "regulator disclosures"]


def apply_freshness_requirements(question: str, claim_ledger: list[ClaimVerification]) -> None:
    if not is_current_information_question(question):
        return
    for entry in claim_ledger:
        spans = entry.supporting_spans
        if not spans:
            if "current-information question has no supporting evidence spans" not in entry.caveats:
                entry.caveats.append("current-information question has no supporting evidence spans")
            continue
        buckets = {span.extra.get("freshness_bucket", "missing_date") for span in spans}
        if buckets <= {"historical", "missing_date"}:
            if entry.status == "supported":
                entry.status = "mixed"
                entry.confidence = min(entry.confidence, 0.62)
            entry.caveats.append("current claim lacks recent_30d or recent_90d evidence; historical/missing_date evidence is background only")


def is_current_information_question(question: str) -> bool:
    lowered = question.lower()
    return any(
        needle in question or needle in lowered
        for needle in ["最近", "最新", "近30天", "近 30 天", "本月", "本季度", "2026", "latest", "recent"]
    )


def source_health_diagnostics(required_sources: list[str], health: dict) -> list[dict]:
    rows: list[dict] = []
    sources = health.get("sources", {})
    for source in required_sources:
        status = sources.get(source)
        if not status:
            rows.append(
                {
                    "source": source,
                    "ok": False,
                    "adapter_mode": "unknown",
                    "error": "source_health missing source",
                    "source_health": True,
                }
            )
            continue
        rows.append(
            {
                "source": source,
                "ok": bool(status.get("ok")),
                "adapter_mode": status.get("adapter_mode", "unknown"),
                "error": "; ".join(status.get("notes") or []) or None,
                "source_health": True,
            }
        )
    return rows


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
