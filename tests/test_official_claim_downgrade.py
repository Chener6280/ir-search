from __future__ import annotations

from ir_search.evidence.models import ClaimVerification, EvidenceSpan
from ir_search.models import EvidenceType, Hit, Query, SearchResult, SourceStatus, SourceTier
from ir_search.research.orchestrator import apply_official_gap_claim_downgrades, deep_research


def test_financial_report_claim_downgraded_without_official_document():
    claim = ClaimVerification(
        "c1",
        "公司季报验证海外 AI 光模块需求强劲",
        "mixed",
        0.62,
        supporting_spans=[_media_span("媒体称海外 AI 光模块需求强劲。")],
    )

    apply_official_gap_claim_downgrades(
        "中际旭创 最新季报是否验证海外 AI 光模块需求？",
        _insufficient_gap_report(),
        [claim],
    )

    assert claim.status == "insufficient_evidence"
    assert claim.confidence <= 0.35
    assert any("official claim requires fetched official document evidence" in caveat for caveat in claim.caveats)


def test_media_signal_claim_can_remain_mixed():
    claim = ClaimVerification(
        "c1",
        "媒体层面存在 AI 光模块需求相关公开讨论",
        "mixed",
        0.55,
        supporting_spans=[_media_span("媒体称海外 AI 光模块需求强劲。")],
    )

    apply_official_gap_claim_downgrades("该需求是否被官方一手证据确认？", _insufficient_gap_report(), [claim])

    assert claim.status == "mixed"


def test_official_confirmation_claim_requires_official_span():
    claim = ClaimVerification(
        "c1",
        "官方公告确认新增订单",
        "supported",
        0.84,
        supporting_spans=[_official_span("公司公告确认新增订单。")],
    )
    report = {
        "verdict": "primary_source_evidence_present",
        "actual_retrieval": {"cninfo": {"searched": True, "fetched_documents": 1, "evidence_spans": 1}},
    }

    apply_official_gap_claim_downgrades("官方公告是否确认新增订单？", report, [claim])

    assert claim.status == "supported"


def test_deep_research_downgrades_official_claims_without_official_documents():
    def search_fn(q: Query) -> SearchResult:
        if q.sources:
            return SearchResult(
                query=q,
                hits=[],
                diagnostics=[SourceStatus(source, True, 0, None, 1, adapter_mode="live") for source in q.sources],
            )
        hit = Hit(
            title="媒体报道",
            url="https://example.com/media",
            snippet="媒体层面存在 AI 光模块需求相关公开讨论。",
            source="industry_media",
            tier=SourceTier.MEDIA,
            evidence_type=EvidenceType.NEWS,
            extra={
                "content": "媒体层面存在 AI 光模块需求相关公开讨论，行业热度被报道。",
                "extraction_method": "fixture",
            },
        )
        return SearchResult(
            query=q,
            hits=[hit],
            diagnostics=[SourceStatus("industry_media", True, 1, None, 1, adapter_mode="live")],
        )

    run = deep_research(
        "中际旭创 最新季报是否验证海外 AI 光模块需求？",
        intent="earnings",
        max_searches=2,
        max_documents=2,
        search_fn=search_fn,
        source_health_fn=lambda: {"sources": {"cninfo": {"ok": True, "adapter_mode": "live"}}},
    )

    official_claims = [
        entry
        for entry in run.claim_ledger
        if any(term in entry.claim for term in ["季报", "业绩", "官方", "公告", "订单"])
    ]
    media_claims = [entry for entry in run.claim_ledger if "媒体层面存在" in entry.claim]

    assert run.extra["official_gap_report"]["verdict"] == "insufficient_primary_source_evidence"
    assert official_claims
    assert all(entry.status == "insufficient_evidence" for entry in official_claims)
    assert media_claims and media_claims[0].status == "mixed"
    assert any(row["final_status"] == "insufficient_evidence" for row in run.source_matrix if "季报" in row["claim"])


def _insufficient_gap_report() -> dict:
    return {
        "verdict": "insufficient_primary_source_evidence",
        "actual_retrieval": {
            "cninfo": {"searched": True, "fetched_documents": 0, "evidence_spans": 0},
            "company_ir": {"searched": False, "fetched_documents": 0, "evidence_spans": 0},
        },
    }


def _media_span(text: str) -> EvidenceSpan:
    return EvidenceSpan(
        span_id="sp_media",
        doc_id="doc_media",
        url="https://example.com/media",
        title="媒体报道",
        source="media",
        source_tier=SourceTier.MEDIA,
        evidence_type=EvidenceType.NEWS,
        text=text,
        relevance_score=0.8,
    )


def _official_span(text: str) -> EvidenceSpan:
    return EvidenceSpan(
        span_id="sp_official",
        doc_id="doc_official",
        url="https://www.cninfo.com.cn/report.pdf",
        title="公司公告",
        source="cninfo",
        source_tier=SourceTier.EXCHANGE_FILING,
        evidence_type=EvidenceType.FINANCIAL_REPORT,
        text=text,
        relevance_score=0.9,
    )
