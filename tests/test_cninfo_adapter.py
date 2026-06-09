import json
from pathlib import Path

import pytest

from ir_search.adapters.base import AdapterError
from ir_search.adapters.cninfo import build_cninfo_params, parse_cninfo_response
from ir_search.models import EvidenceType, Query, SourceTier
from ir_search.pipeline import prepare_query


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "cninfo"


def _fixture(name: str):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_cninfo_parse_announcement_rows():
    hits = parse_cninfo_response(_fixture("search_300308_announcements.json"), 10)

    assert len(hits) == 2
    assert hits[0].source == "cninfo"
    assert hits[0].tier == SourceTier.EXCHANGE_FILING
    assert hits[0].evidence_type == EvidenceType.ANNOUNCEMENT
    assert hits[0].published_at is not None
    assert hits[0].url.startswith("https://static.cninfo.com.cn/")
    assert hits[0].extra["security_code"] == "300308"
    assert hits[0].extra["security_name"] == "中际旭创"
    assert hits[0].extra["source_platform"] == "cninfo"


def test_cninfo_financial_report_evidence_type():
    hits = parse_cninfo_response(_fixture("search_300308_financial_reports.json"), 10)

    assert hits[0].evidence_type == EvidenceType.FINANCIAL_REPORT
    assert "第一季度报告" in hits[0].title


def test_cninfo_missing_optional_fields():
    data = {
        "announcements": [
            {
                "announcementTitle": "中际旭创：公告",
                "adjunctUrl": "finalpage/2026-01-01/a.PDF",
            }
        ]
    }

    hit = parse_cninfo_response(data, 1)[0]
    assert "missing_security_code" in hit.extra["parse_warning"]
    assert hit.url.endswith("a.PDF")


def test_cninfo_invalid_response_raises_adapter_error():
    with pytest.raises(AdapterError, match="announcements"):
        parse_cninfo_response(_fixture("error_response.json"), 10)


def test_cninfo_security_code_query():
    q = prepare_query(Query(text="300308 一季报"))
    params = build_cninfo_params(q)

    assert params["stock"] == ""
    assert params["column"] == "szse"
    assert params["searchkey"] == "300308 一季报"


def test_cninfo_latest_announcement_searchkey_drops_generic_words():
    q = prepare_query(Query(text="中际旭创 最新公告"))
    params = build_cninfo_params(q)

    assert params["searchkey"] == "300308"
