from ir_search.evidence import EvidenceSpan, verify_claims
from ir_search.evidence.verifier import looks_contradictory
from ir_search.models import EvidenceType, SourceTier


def test_no_false_contradiction_on_buduan_zengzhang():
    assert looks_contradictory("海外需求强劲", "公司表示未来需求不断增长。") is False


def test_no_false_contradiction_on_weilai_xuqiu():
    assert looks_contradictory("海外需求强劲", "未来需求仍有增长空间。") is False


def test_no_false_contradiction_on_butong_yewu():
    assert looks_contradictory("公司业务增长", "不同业务均实现增长。") is False


def test_no_false_contradiction_on_risk_disclosure():
    result = verify_claims(
        ["风险整体可控"],
        evidence_spans=[_span("公司提示风险，但经营趋势保持稳定。")],
    )

    assert result[0].status != "contradicted"
    assert any("risk" in caveat.lower() or "uncertainty" in caveat.lower() for caveat in result[0].caveats)


def test_explicit_refutation_is_contradiction():
    result = verify_claims(
        ["订单已经确认增长"],
        evidence_spans=[_span("公司否认相关订单增长传闻。")],
    )

    assert result[0].status == "contradicted"


def test_directional_metric_conflict_is_contradiction():
    result = verify_claims(
        ["收入增长"],
        evidence_spans=[_span("公司披露收入同比下降。")],
    )

    assert result[0].status == "contradicted"


def test_risk_only_becomes_caveat_not_contradiction():
    result = verify_claims(
        ["经营趋势保持稳定"],
        evidence_spans=[_span("公司提示风险，但经营趋势保持稳定。")],
    )

    assert result[0].status != "contradicted"
    assert result[0].caveats


def test_no_evidence_phrase_is_contradiction():
    result = verify_claims(
        ["需求强劲"],
        evidence_spans=[_span("没有证据显示需求强劲。")],
    )

    assert result[0].status == "contradicted"


def _span(text):
    return EvidenceSpan(
        span_id="sp1",
        doc_id="doc1",
        url="https://example.com/a",
        title="title",
        source="cninfo",
        source_tier=SourceTier.EXCHANGE_FILING,
        evidence_type=EvidenceType.FINANCIAL_REPORT,
        text=text,
        relevance_score=0.8,
    )
