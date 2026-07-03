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
    effective_freshness = effective_freshness_for_question(question, freshness)
    freshness_policy = build_freshness_policy(question, freshness, effective_freshness)
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
    reserved_official_budget = min(max(1, len(plan.official_queries)), max_searches - 1) if plan.required_sources and max_searches > 1 else 0
    initial_search_budget = max(1, max_searches - reserved_official_budget) if plan.required_sources else max_searches
    for query_text in plan.queries[:initial_search_budget]:
        q = Query(
            text=query_text,
            count=max_documents,
            window=TimeWindow(raw=effective_freshness),
            intent=_intent_from_string(plan.intent),
            allow_fallback=True,
            fallback_policy=FallbackPolicy.QUOTA_ONLY,
            fallback_on_empty=False,
        )
        result = search_fn(q)
        append_search_log(search_log, query_text=query_text, result=result, official_only=False)
        diagnostics.extend(source_statuses_from_result(result))
        for hit in result.hits:
            key = hit.canonical_url or hit.url
            hits_by_url.setdefault(key, hit)
        if len(hits_by_url) >= max_documents:
            break

    source_capabilities = build_source_capabilities(plan.required_sources, health)
    official_second_pass = maybe_run_official_second_pass(
        question=question,
        freshness=effective_freshness,
        max_documents=max_documents,
        max_searches=max_searches,
        search_log=search_log,
        hits_by_url=hits_by_url,
        required_sources=plan.required_sources,
        source_capabilities=source_capabilities,
        intent=plan.intent,
        official_queries=plan.official_queries,
        search_fn=search_fn,
    )
    diagnostics.extend(official_second_pass.get("diagnostics", []))

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
    apply_official_gap_claim_downgrades(question, official_gap_report, claim_ledger)
    source_matrix = build_source_matrix(claim_ledger)
    language_mix_policy = build_language_mix_policy(question, plan.queries + plan.official_queries)
    wechat_crosscheck = build_wechat_crosscheck(question, evidence_spans, claim_ledger)
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
        official_second_pass=official_second_pass,
        language_mix_policy=language_mix_policy,
        wechat_crosscheck=wechat_crosscheck,
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
            "freshness_policy": freshness_policy,
            "language_mix_policy": language_mix_policy,
            "wechat_crosscheck": wechat_crosscheck,
            "reserved_parameters": reserved_parameters,
            "claim_candidates": claim_candidates,
            "plan_warnings": plan.warnings,
            "official_queries": plan.official_queries,
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


def append_search_log(
    search_log: list[dict],
    *,
    query_text: str,
    result: SearchResult,
    official_only: bool,
) -> None:
    statuses = source_statuses_from_result(result)
    search_log.append(
        {
            "query": query_text,
            "n_hits": len(result.hits),
            "sources": [status.get("source") for status in statuses if status.get("source")],
            "hit_sources": sorted({hit.source for hit in result.hits}),
            "official_only": official_only,
            "source_statuses": statuses,
        }
    )


def source_statuses_from_result(result: SearchResult) -> list[dict]:
    return list(result.to_dict().get("diagnostics") or [])


def maybe_run_official_second_pass(
    *,
    question: str,
    freshness: str,
    max_documents: int,
    max_searches: int,
    search_log: list[dict],
    hits_by_url: dict[str, Hit],
    required_sources: list[str],
    source_capabilities: dict[str, dict],
    intent: Optional[str],
    official_queries: list[str],
    search_fn: Callable[[Query], SearchResult],
) -> dict:
    placeholder_details = placeholder_source_details(required_sources, source_capabilities)
    not_attempted_sources = [
        {
            "source": source,
            "capability": source_capabilities.get(source, {}).get("adapter_mode", "unknown"),
            "attempted": False,
            "reason": "pending_official_second_pass",
        }
        for source in required_sources
    ]
    if not required_sources:
        return {
            "triggered": False,
            "reason": "no_required_official_sources",
            "query": "",
            "required_sources": [],
            "source_statuses": [],
            "diagnostics": [],
            "n_hits": 0,
            "not_attempted_sources": [],
        }
    if len(search_log) >= max_searches:
        return {
            "triggered": False,
            "reason": "search_budget_exhausted",
            "source_statuses": [],
            "diagnostics": [],
            "placeholder_sources": placeholder_details,
            "not_attempted_sources": not_attempted_sources,
            "required_sources": required_sources,
            "n_hits": 0,
        }
    if any(hit.tier >= SourceTier.COMPANY for hit in hits_by_url.values()):
        return {
            "triggered": False,
            "reason": "official_hit_already_present",
            "source_statuses": [],
            "diagnostics": [],
            "placeholder_sources": placeholder_details,
            "not_attempted_sources": not_attempted_sources,
            "required_sources": required_sources,
            "n_hits": 0,
        }

    query_texts = official_queries or [f"{question} 官方公告 交易所 监管 披露"]
    available_budget = max(0, max_searches - len(search_log))
    source_statuses: list[dict] = []
    executed_queries: list[str] = []
    n_hits = 0
    for query_text in query_texts[:available_budget]:
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
        statuses = source_statuses_from_result(result)
        source_statuses.extend(statuses)
        executed_queries.append(query_text)
        n_hits += len(result.hits)
        append_search_log(search_log, query_text=query_text, result=result, official_only=True)
        for hit in result.hits:
            key = hit.canonical_url or hit.url
            hits_by_url.setdefault(key, hit)
    return {
        "triggered": True,
        "reason": "primary_sources_missing",
        "query": executed_queries[0] if executed_queries else "",
        "queries": executed_queries,
        "not_attempted_queries": query_texts[available_budget:],
        "required_sources": required_sources,
        "n_hits": n_hits,
        "source_statuses": source_statuses,
        "diagnostics": source_statuses,
        "placeholder_sources": placeholder_details,
        "not_attempted_sources": [],
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
            "availability_reason": status.get("availability_reason"),
            "notes": list(status.get("notes") or []),
            "diagnostics": status.get("diagnostics") or {},
        }
    return capabilities


def build_actual_evidence_by_source(
    search_log: list[dict],
    documents: list[Document],
    evidence_spans: list[EvidenceSpan],
    claim_ledger: list[ClaimVerification],
) -> dict[str, dict]:
    status_rows_by_source: dict[str, list[dict]] = {}
    for item in search_log:
        for status in item.get("source_statuses") or []:
            if not isinstance(status, dict):
                continue
            source = status.get("source")
            if source:
                status_rows_by_source.setdefault(source, []).append(status)
    searched_sources = {
        source
        for item in search_log
        for source in (item.get("sources") or []) + (item.get("hit_sources") or [])
        if source
    }
    sources = (
        searched_sources
        | set(status_rows_by_source)
        | {document.source for document in documents}
        | {span.source for span in evidence_spans}
    )
    supporting_claims_by_source: dict[str, set[str]] = {}
    for entry in claim_ledger:
        for span in entry.supporting_spans:
            if is_mock_or_placeholder(span.extra.get("adapter_mode")):
                continue
            supporting_claims_by_source.setdefault(span.source, set()).add(entry.claim)

    matrix: dict[str, dict] = {}
    for source in sorted(sources):
        source_documents = [document for document in documents if document.source == source]
        status_rows = status_rows_by_source.get(source, [])
        fetched_documents = [
            document
            for document in source_documents
            if document.text.strip()
            and not document.errors
            and document.content_type != "snippet"
            and document.extraction_method != "search_hit_snippet_fallback"
            and not is_mock_or_placeholder(document.extra.get("adapter_mode"))
        ]
        source_spans = [
            span
            for span in evidence_spans
            if span.source == source and not is_mock_or_placeholder(span.extra.get("adapter_mode"))
        ]
        fetch_errors = document_fetch_errors(source_documents)
        source_tiers = sorted({document.source_tier.name for document in source_documents} | {span.source_tier.name for span in source_spans})
        evidence_types = sorted({document.evidence_type.value for document in source_documents} | {span.evidence_type.value for span in source_spans})
        content_types = sorted({document.content_type for document in source_documents if document.content_type})
        matrix[source] = {
            "searched": source in searched_sources,
            "search_attempts": len(status_rows),
            "search_ok": any(bool(row.get("ok")) for row in status_rows) if status_rows else None,
            "search_errors": [str(row.get("error")) for row in status_rows if row.get("error")],
            "adapter_modes": sorted({str(row.get("adapter_mode")) for row in status_rows if row.get("adapter_mode")}),
            "n_results": sum(int(row.get("n_results", 0) or 0) for row in status_rows),
            "documents_seen": len(source_documents),
            "fetched_documents": len(fetched_documents),
            "fetch_errors": fetch_errors,
            "evidence_spans": len(source_spans),
            "supporting_claims": sorted(supporting_claims_by_source.get(source, set())),
            "source_tiers": source_tiers,
            "evidence_types": evidence_types,
            "content_types": content_types,
            "text_basis": text_basis_for_documents(source_documents, fetched_documents),
        }
    return matrix


def is_mock_or_placeholder(adapter_mode: object) -> bool:
    return str(adapter_mode or "").lower() in {"mock", "placeholder"}


def document_fetch_errors(documents: list[Document]) -> list[str]:
    errors: list[str] = []
    for document in documents:
        errors.extend(str(error) for error in document.errors if error)
        for warning in document.warnings:
            lowered = warning.lower()
            if "fetch" in lowered or "http error" in lowered:
                errors.append(str(warning))
    return errors


def text_basis_for_documents(source_documents: list[Document], fetched_documents: list[Document]) -> str:
    if not source_documents:
        return "none"
    if fetched_documents:
        return "full_document_or_local_text"
    if all(document.content_type == "snippet" for document in source_documents):
        return "snippet_only"
    return "unfetched_or_failed"


def build_official_source_attempts(
    required_sources: list[str],
    source_capabilities: dict[str, dict],
    actual_evidence_by_source: dict[str, dict],
) -> list[dict]:
    attempts: list[dict] = []
    for source in required_sources:
        capability = source_capabilities.get(source, {})
        actual = actual_evidence_by_source.get(source, {})
        attempts.append(official_attempt_row(source, capability, actual))
    return attempts


def official_attempt_row(source: str, capability: dict, actual: dict) -> dict:
    fetched_documents = int(actual.get("fetched_documents", 0) or 0)
    evidence_spans = int(actual.get("evidence_spans", 0) or 0)
    searched = bool(actual.get("searched"))
    fetch_errors = list(actual.get("fetch_errors") or [])
    search_errors = list(actual.get("search_errors") or [])
    if evidence_spans:
        status = "evidence_retrieved"
        reason = "evidence_retrieved"
    elif fetched_documents:
        status = "document_fetched_no_evidence_spans"
        reason = "document_fetched_no_evidence_spans"
    elif fetch_errors:
        status = "fetch_error"
        reason = "fetch_error"
    elif searched and search_errors:
        status = "adapter_error"
        reason = "adapter_error"
    elif searched:
        status = "searched_no_evidence_retrieved"
        reason = "not_found"
    elif capability.get("adapter_mode") == "placeholder":
        status = "source_unavailable_or_placeholder"
        reason = "adapter_not_implemented"
    elif capability.get("adapter_mode") == "mock":
        status = "source_unavailable_or_placeholder"
        reason = "adapter_not_live"
    elif capability.get("adapter_mode") == "unknown" or not capability.get("ok", False):
        status = "source_unavailable_or_placeholder"
        reason = capability.get("availability_reason") or "source_unavailable"
    else:
        status = "not_attempted"
        reason = "not_attempted"
    return {
        "source": source,
        "capability": capability.get("adapter_mode", "unknown"),
        "availability_reason": capability.get("availability_reason"),
        "official_attempted": searched,
        "searched": searched,
        "fetched_documents": fetched_documents,
        "evidence_spans": evidence_spans,
        "search_errors": search_errors,
        "fetch_errors": fetch_errors,
        "status": status,
        "reason": reason,
    }


def placeholder_source_details(required_sources: list[str], source_capabilities: dict[str, dict]) -> list[dict]:
    details: list[dict] = []
    for source in required_sources:
        capability = source_capabilities.get(source, {})
        if capability.get("adapter_mode") in {"mock", "placeholder", "unknown"} or not capability.get("ok", False):
            details.append(
                {
                    "source": source,
                    "capability": capability.get("adapter_mode", "unknown"),
                    "attempted": False,
                    "reason": "adapter_not_live" if capability.get("adapter_mode") in {"mock", "placeholder"} else "source_unavailable",
                }
            )
    return details


def build_official_gap_report(
    question: str,
    required_sources: list[str],
    source_capabilities: dict,
    actual_evidence_by_source: dict,
    claim_ledger: list[ClaimVerification],
) -> dict:
    official_sources_required = required_sources or default_official_sources_for_question(question)
    actual_retrieval = {
        source: official_actual_retrieval_row(
            source,
            source_capabilities.get(source, {}),
            actual_evidence_by_source.get(source, {}),
        )
        for source in official_sources_required
    }
    official_sources_with_evidence = [
        source
        for source in official_sources_required
        if actual_retrieval.get(source, {}).get("evidence_spans", 0) > 0
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
        "required_for_claims": required_for_claims(question, claim_ledger),
        "verdict": verdict,
        "official_sources_required": official_sources_required,
        "source_capability": {source: source_capabilities.get(source, {}) for source in official_sources_required},
        "actual_retrieval": actual_retrieval,
        "official_attempted": any(row.get("official_attempted") for row in actual_retrieval.values()),
        "official_sources_with_evidence": official_sources_with_evidence,
        "official_supported_claims": official_supported_claims,
        "manual_checklist": manual_checklist_for_official_gap(question, official_sources_required),
    }


def official_actual_retrieval_row(source: str, capability: dict, actual: dict) -> dict:
    row = {
        "searched": False,
        "search_attempts": 0,
        "search_ok": None,
        "search_errors": [],
        "adapter_modes": [],
        "n_results": 0,
        "documents_seen": 0,
        "fetched_documents": 0,
        "fetch_errors": [],
        "evidence_spans": 0,
        "supporting_claims": [],
        "source_tiers": [],
        "evidence_types": [],
        "content_types": [],
        "text_basis": "none",
    }
    row.update(actual or {})
    attempt = official_attempt_row(source, capability, row)
    row.update(
        {
            "official_attempted": attempt["official_attempted"],
            "status": attempt["status"],
            "reason": attempt["reason"],
        }
    )
    return row


def required_for_claims(question: str, claim_ledger: list[ClaimVerification]) -> list[str]:
    required: list[str] = []
    for entry in claim_ledger:
        has_primary_support = any(span.source_tier >= SourceTier.COMPANY for span in entry.supporting_spans)
        if has_primary_support:
            continue
        if is_current_information_question(question) or claim_needs_official_source(entry.claim):
            required.append(entry.claim)
    if not required and claim_needs_official_source(question):
        required.append(question)
    return required


def apply_official_gap_claim_downgrades(
    question: str,
    official_gap_report: dict,
    claim_ledger: list[ClaimVerification],
) -> None:
    if official_gap_report.get("verdict") != "insufficient_primary_source_evidence":
        return
    if not _official_retrieval_has_gap(official_gap_report):
        return
    for entry in claim_ledger:
        if not (claim_needs_official_source(entry.claim) or _question_makes_claim_official(question, entry.claim)):
            continue
        if entry.status not in {"supported", "mixed"}:
            continue
        entry.status = "insufficient_evidence"
        entry.confidence = min(entry.confidence, 0.35)
        caveat = "official claim requires fetched official document evidence; official_gap_report shows insufficient primary source evidence"
        if caveat not in entry.caveats:
            entry.caveats.append(caveat)


def _official_retrieval_has_gap(official_gap_report: dict) -> bool:
    actual = official_gap_report.get("actual_retrieval") or {}
    if not actual:
        return True
    fetched_documents = 0
    evidence_spans = 0
    for row in actual.values():
        if not isinstance(row, dict):
            continue
        fetched_documents += int(row.get("fetched_documents", 0) or 0)
        evidence_spans += int(row.get("evidence_spans", 0) or 0)
    return fetched_documents == 0 or evidence_spans == 0


def _question_makes_claim_official(question: str, claim: str) -> bool:
    if not claim_needs_official_source(question):
        return False
    return claim_needs_official_source(claim)


def claim_needs_official_source(claim: str) -> bool:
    lowered = claim.lower()
    return any(
        needle in claim
        for needle in ["官方", "公告", "季报", "年报", "财报", "业绩", "订单", "确认", "披露", "监管", "政策"]
    ) or any(
        needle in lowered
        for needle in [
            "official confirmation",
            "official evidence",
            "primary source",
            "company filing",
            "financial report",
            "earnings report",
            "exchange filing",
            "regulator",
        ]
    )


def build_language_mix_policy(question: str, queries: list[str]) -> dict:
    query_language = detect_language(question)
    expanded_queries = [{"query": query, "language": detect_language(query), "reason": "planned deterministic query"} for query in queries]
    disclosure = "No cross-language expansion was needed."
    if query_language == "en" and looks_china_supply_chain_question(question):
        zh_query = f"{question} 中国 A股 产业链 公司公告"
        expanded_queries.append(
            {
                "query": zh_query,
                "language": "zh",
                "reason": "China-listed supply-chain coverage",
            }
        )
        disclosure = "Chinese sources were used for China-listed supply-chain coverage; they are not overseas official evidence."
    return {
        "query_language": query_language,
        "expanded_queries": expanded_queries,
        "disclosure": disclosure,
    }


def detect_language(text: str) -> str:
    has_zh = any("\u4e00" <= char <= "\u9fff" for char in text)
    has_ascii_word = any(char.isascii() and char.isalpha() for char in text)
    if has_zh and has_ascii_word:
        return "mixed"
    if has_zh:
        return "zh"
    if has_ascii_word:
        return "en"
    return "unknown"


def looks_china_supply_chain_question(question: str) -> bool:
    lowered = question.lower()
    return any(term in lowered for term in ["ai", "optical", "module", "china", "supply chain", "overseas"]) or any(
        term in question for term in ["光模块", "产业链", "海外", "中国", "A股"]
    )


def build_wechat_crosscheck(
    question: str,
    evidence_spans: list[EvidenceSpan],
    claim_ledger: list[ClaimVerification],
) -> dict:
    if not looks_wechat_crosscheck_question(question):
        return {
            "applicable": False,
            "wechat_evidence": [],
            "media_crosscheck": [],
            "official_crosscheck": [],
            "verdict": "not_applicable",
        }
    wechat_evidence = [span_summary(span) for span in evidence_spans if _matrix_column(span) == "wechat"]
    media_crosscheck = [span_summary(span) for span in evidence_spans if _matrix_column(span) == "media"]
    official_crosscheck = [span_summary(span) for span in evidence_spans if span.source_tier >= SourceTier.COMPANY]
    if official_crosscheck and any(entry.status == "supported" for entry in claim_ledger):
        verdict = "supported_by_official"
    elif media_crosscheck or official_crosscheck:
        verdict = "mixed"
    elif wechat_evidence:
        verdict = "candidate_only"
    else:
        verdict = "insufficient_evidence"
    return {
        "applicable": True,
        "wechat_evidence": wechat_evidence,
        "media_crosscheck": media_crosscheck,
        "official_crosscheck": official_crosscheck,
        "verdict": verdict,
    }


def looks_wechat_crosscheck_question(question: str) -> bool:
    lowered = question.lower()
    return any(term in lowered for term in ["wechat", "rumor"]) or any(term in question for term in ["微信", "公众号", "涨价传闻", "自媒体", "传闻"])


def span_summary(span: EvidenceSpan) -> dict:
    return {
        "span_id": span.span_id,
        "source": span.source,
        "source_tier": span.source_tier.name,
        "evidence_type": span.evidence_type.value,
        "title": span.title,
        "url": span.url,
        "freshness_bucket": span.extra.get("freshness_bucket", "missing_date"),
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


def effective_freshness_for_question(question: str, requested_freshness: str) -> str:
    lower = question.lower()
    if any(needle in question or needle in lower for needle in ["近90天", "近 90 天", "90天", "90 天", "recent 90", "90d"]):
        return "90d"
    return requested_freshness


def build_freshness_policy(question: str, requested_freshness: str, effective_freshness: str) -> dict:
    current_info = is_current_information_question(question)
    return {
        "requested_freshness": requested_freshness,
        "effective_freshness": effective_freshness,
        "current_information_question": current_info,
        "allowed_support_buckets": ["recent_30d", "recent_90d"] if current_info else [],
        "stale_buckets_background_only": ["historical", "missing_date"] if current_info else [],
    }


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
            if entry.status in {"supported", "mixed"}:
                entry.status = "insufficient_evidence"
                entry.confidence = min(entry.confidence, 0.35)
            caveat = "current claim lacks recent_30d or recent_90d evidence; historical/missing_date evidence is background only"
            if caveat not in entry.caveats:
                entry.caveats.append(caveat)


def is_current_information_question(question: str) -> bool:
    lowered = question.lower()
    return any(
        needle in question or needle in lowered
        for needle in [
            "最近",
            "最新",
            "当前",
            "近30天",
            "近 30 天",
            "近90天",
            "近 90 天",
            "本月",
            "本季度",
            "2026",
            "latest",
            "recent",
            "current",
            "90d",
        ]
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
