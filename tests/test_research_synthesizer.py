from ir_search.evidence.models import ClaimVerification, EvidenceSpan
from ir_search.models import EvidenceType, SourceTier
from ir_search.research.synthesizer import synthesize_answer


def test_supported_claims_stated_as_facts():
    answer = _answer([ClaimVerification("c1", "公司收入增长", "supported", 0.8)])

    assert "[supported 0.80] 公司收入增长" in answer


def test_mixed_claims_stated_with_caveat():
    answer = _answer([ClaimVerification("c1", "公司收入增长", "mixed", 0.6, caveats=["仅摘要支持"])])

    assert "[mixed 0.60] 公司收入增长 Caveat: 仅摘要支持" in answer


def test_insufficient_claims_not_written_as_facts():
    answer = _answer([ClaimVerification("c1", "公司收入增长", "insufficient_evidence", 0.0)])

    assert "[insufficient_evidence 0.00] 公司收入增长" in answer


def test_diagnostics_appear_before_conclusion():
    answer = _answer([ClaimVerification("c1", "公司收入增长", "supported", 0.8)])

    assert answer.index("检索状态") < answer.index("核心结论")


def test_unverified_items_are_visible():
    answer = _answer([ClaimVerification("c1", "公司收入增长", "supported", 0.8)], unverified_items=["缺少官方全文"])

    assert "缺少官方全文" in answer


def test_synthesizer_dedupes_evidence_rows():
    span = _span()
    claim = ClaimVerification("c1", "公司收入增长", "supported", 0.8, supporting_spans=[span, span])

    answer = _answer([claim])

    assert answer.count("公司公告显示收入增长") == 1


def test_synthesizer_uses_claim_text_not_claim_id_in_evidence_table():
    claim = ClaimVerification("c1_secretish", "公司收入增长", "supported", 0.8, supporting_spans=[_span()])

    answer = _answer([claim])

    table = answer.split("证据表", 1)[1]
    assert "| 公司收入增长 | supported |" in table
    assert "| c1_secretish |" not in table


def test_synthesizer_includes_freshness_bucket():
    answer = _answer([ClaimVerification("c1", "公司收入增长", "supported", 0.8, supporting_spans=[_span()])])

    assert "recent_30d" in answer


def test_synthesizer_discloses_official_gap_report():
    answer = _answer(
        [ClaimVerification("c1", "公司收入增长", "mixed", 0.5)],
        official_gap_report={
            "verdict": "insufficient_primary_source_evidence",
            "official_sources_required": ["cninfo"],
            "official_sources_with_evidence": [],
            "manual_checklist": ["cninfo announcements"],
        },
    )

    assert "official_gap_report" in answer
    assert "insufficient_primary_source_evidence" in answer


def _answer(claims, unverified_items=None, official_gap_report=None):
    return synthesize_answer(
        run_id="run1",
        question="question",
        search_log=[],
        evidence_spans=[],
        claim_ledger=claims,
        source_matrix=[],
        diagnostics=[],
        unverified_items=unverified_items or [],
        official_gap_report=official_gap_report,
    )


def _span():
    return EvidenceSpan(
        span_id="sp1",
        doc_id="doc1",
        url="https://example.com/a",
        title="公司公告",
        source="company_ir",
        source_tier=SourceTier.COMPANY,
        evidence_type=EvidenceType.ANNOUNCEMENT,
        text="公司公告显示收入增长。",
        relevance_score=0.8,
        extra={"freshness_bucket": "recent_30d"},
    )
