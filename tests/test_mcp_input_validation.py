from ir_search.mcp_server import parse_evidence_span, verify_claims_payload


def test_parse_evidence_span_invalid_source_tier():
    span, errors = parse_evidence_span({**_span_dict(), "source_tier": "BAD_TIER"}, index=0)

    assert span is None
    assert errors[0]["field"] == "source_tier"
    assert errors[0]["index"] == 0


def test_parse_evidence_span_invalid_evidence_type():
    span, errors = parse_evidence_span({**_span_dict(), "evidence_type": "bad_type"}, index=0)

    assert span is None
    assert errors[0]["field"] == "evidence_type"


def test_parse_evidence_span_invalid_published_at():
    span, errors = parse_evidence_span({**_span_dict(), "published_at": "not-a-date"}, index=0)

    assert span is None
    assert errors[0]["field"] == "published_at"


def test_parse_evidence_span_missing_required_field():
    data = _span_dict()
    data.pop("text")

    span, errors = parse_evidence_span(data, index=0)

    assert span is None
    assert errors[0]["field"] == "text"


def test_parse_evidence_span_ignores_extra_fields():
    span, errors = parse_evidence_span({**_span_dict(), "unexpected": "ignored"}, index=0)

    assert errors == []
    assert span is not None
    assert span.text == "公司收入增长。"


def test_verify_claims_payload_returns_structured_errors_for_bad_spans():
    payload = verify_claims_payload(
        ["公司收入增长"],
        evidence_spans=[{**_span_dict(), "source_tier": "BAD_TIER"}],
    )

    assert payload["claim_ledger"][0]["status"] == "insufficient_evidence"
    assert payload["errors"][0]["code"] == "invalid_evidence_span"


def test_verify_claims_payload_continues_with_valid_spans():
    payload = verify_claims_payload(
        ["公司收入增长"],
        evidence_spans=[
            {**_span_dict(), "source_tier": "BAD_TIER"},
            _span_dict(),
        ],
    )

    assert payload["errors"]
    assert payload["claim_ledger"][0]["status"] == "supported"


def _span_dict():
    return {
        "span_id": "sp1",
        "doc_id": "doc1",
        "url": "https://example.com/a",
        "title": "title",
        "source": "cninfo",
        "source_tier": "EXCHANGE_FILING",
        "evidence_type": "financial_report",
        "text": "公司收入增长。",
        "relevance_score": 0.8,
        "published_at": "2026-07-01T00:00:00+00:00",
        "extra": {},
    }
