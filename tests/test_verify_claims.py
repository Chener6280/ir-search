from ir_search.evidence import EvidenceSpan, verify_claims
from ir_search.models import EvidenceType, SourceTier


def test_official_span_supports_claim():
    span = _span("公司收入增长，海外 AI 光模块需求强劲。", SourceTier.EXCHANGE_FILING, "cninfo")

    result = verify_claims(["公司收入增长，海外 AI 光模块需求强劲"], evidence_spans=[span])

    assert result[0].status == "supported"
    assert result[0].supporting_spans == [span]


def test_media_only_support_is_mixed_with_caveat():
    span = _span("公众号称产业链涨价已经验证需求强劲。", SourceTier.MEDIA, "manual_wechat")

    result = verify_claims(["产业链涨价验证需求强劲"], evidence_spans=[span])

    assert result[0].status == "mixed"
    assert result[0].caveats


def test_no_evidence_is_insufficient():
    result = verify_claims(["公司收入增长"], evidence_spans=[])

    assert result[0].status == "insufficient_evidence"


def test_contradicting_span_is_marked():
    span = _span("公司披露需求下滑，订单减少。", SourceTier.EXCHANGE_FILING, "cninfo")

    result = verify_claims(["需求强劲，订单增长"], evidence_spans=[span])

    assert result[0].status == "contradicted"
    assert result[0].contradicting_spans == [span]


def test_confidence_increases_with_independent_sources():
    one = verify_claims(["公司收入增长"], evidence_spans=[_span("公司收入增长。", SourceTier.EXCHANGE_FILING, "cninfo")])
    two = verify_claims(
        ["公司收入增长"],
        evidence_spans=[
            _span("公司收入增长。", SourceTier.EXCHANGE_FILING, "cninfo", url="https://cninfo.com.cn/a"),
            _span("公司收入增长。", SourceTier.COMPANY, "company_ir", url="https://ir.example.com/a"),
        ],
    )

    assert two[0].confidence > one[0].confidence


def test_confidence_penalizes_snippet_only():
    full = _span("公司收入增长。", SourceTier.EXCHANGE_FILING, "cninfo")
    snippet = _span("公司收入增长。", SourceTier.EXCHANGE_FILING, "cninfo")
    snippet.extra["content_type"] = "snippet"

    full_result = verify_claims(["公司收入增长"], evidence_spans=[full])
    snippet_result = verify_claims(["公司收入增长"], evidence_spans=[snippet])

    assert snippet_result[0].confidence < full_result[0].confidence


def test_confidence_penalizes_mock_placeholder():
    real = _span("公司收入增长。", SourceTier.EXCHANGE_FILING, "cninfo")
    mock = _span("公司收入增长。", SourceTier.EXCHANGE_FILING, "cninfo")
    mock.extra["adapter_mode"] = "mock"

    real_result = verify_claims(["公司收入增长"], evidence_spans=[real])
    mock_result = verify_claims(["公司收入增长"], evidence_spans=[mock])

    assert mock_result[0].confidence < real_result[0].confidence
    assert mock_result[0].status == "mixed"


def test_official_source_increases_confidence():
    media = verify_claims(["公司收入增长"], evidence_spans=[_span("公司收入增长。", SourceTier.MEDIA, "media")])
    official = verify_claims(["公司收入增长"], evidence_spans=[_span("公司收入增长。", SourceTier.EXCHANGE_FILING, "cninfo")])

    assert official[0].confidence > media[0].confidence


def _span(text, tier, source, url="https://example.com/a"):
    return EvidenceSpan(
        span_id="sp1",
        doc_id="doc1",
        url=url,
        title="title",
        source=source,
        source_tier=tier,
        evidence_type=EvidenceType.FINANCIAL_REPORT,
        text=text,
        relevance_score=0.8,
    )
