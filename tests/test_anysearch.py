from ir_search.adapters.anysearch import build_anysearch_payload
from ir_search.models import Lang, Query, TimeWindow


def test_anysearch_payload_uses_cn_zone_for_chinese_query():
    q = Query(text="中际旭创 最新", lang=Lang.ZH, count=5, window=TimeWindow(raw="oneWeek"))

    payload = build_anysearch_payload(q)

    assert payload["query"] == "中际旭创 最新"
    assert payload["max_results"] == 5
    assert payload["zone"] == "cn"
    assert payload["language"] == "zh-CN"
    assert payload["domains"] == ["finance", "business"]
    assert payload["content_types"] == ["web", "news"]
    assert payload["constraint"] == {"freshness": "week"}


def test_anysearch_payload_uses_intl_zone_for_english_query():
    q = Query(text="NVIDIA capex", lang=Lang.EN, count=3)

    payload = build_anysearch_payload(q)

    assert payload["zone"] == "intl"
    assert payload["language"] == "en"
