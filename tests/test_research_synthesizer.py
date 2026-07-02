from ir_search.evidence.models import ClaimVerification
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


def _answer(claims, unverified_items=None):
    return synthesize_answer(
        run_id="run1",
        question="question",
        search_log=[],
        evidence_spans=[],
        claim_ledger=claims,
        source_matrix=[],
        diagnostics=[],
        unverified_items=unverified_items or [],
    )
