from ir_search.adapters.mock import MockSearchAdapter
from ir_search.adapters.wechat_opencli import parse_published_at
from ir_search.models import Query


def test_mock_adapter_returns_timezone_aware_published_at():
    hit = MockSearchAdapter("cninfo").query(Query(text="中际旭创"))[0]

    assert hit.published_at is not None
    assert hit.published_at.tzinfo is not None


def test_wechat_opencli_parse_published_at_is_timezone_aware_or_none():
    parsed = parse_published_at("2026-06-08")

    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parse_published_at("not a date") is None
