from ir_search.models import Query
from ir_search.pipeline import prepare_query, select_sources


def _sources(text: str):
    return select_sources(prepare_query(Query(text=text)))


def test_filing_routes_to_official_sources():
    sources = _sources("中际旭创 最新公告")
    assert sources[:3] == ["cninfo", "sse", "szse"]
    assert "bocha" not in sources


def test_broker_research_does_not_default_to_opencli():
    sources = _sources("中际旭创 研报 目标价")
    assert "broker_research" in sources
    assert "wechat_opencli" not in sources


def test_wechat_trigger_is_explicit():
    sources = _sources("某公众号 最新文章 光模块")
    assert "manual_wechat" in sources
    assert "wechat_opencli" in sources


def test_sentiment_routes_by_language():
    assert _sources("中际旭创 股吧 热度") == ["bocha"]
    assert _sources("NVDA StockTwits sentiment") == ["exa"]


def test_forced_unknown_source_is_preserved():
    q = prepare_query(Query(text="中际旭创", sources=["missing_source"]))
    assert select_sources(q) == ["missing_source"]
