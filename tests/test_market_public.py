from ir_search.adapters.market_public import (
    MarketPublicAdapter,
    dataframe_item_value_map,
    parse_baidu_quote,
    parse_eastmoney_quote,
    parse_tencent_statement,
    public_symbol_to_tencent,
    tencent_code_to_public_symbol,
)
from ir_search.kernel import build_registry
from ir_search.models import EvidenceType, Query
from ir_search.pipeline import fallback_sources_for, prepare_query, select_sources


class FakeResponse:
    def __init__(self, text):
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.text.encode("gbk")


def tencent_line(code="sh600519", name="贵州茅台"):
    values = [""] * 60
    values[1] = name
    values[3] = "1500.00"
    values[4] = "1490.00"
    values[5] = "1492.00"
    values[31] = "10.00"
    values[32] = "0.67"
    values[33] = "1510.00"
    values[34] = "1480.00"
    values[37] = "123456"
    values[38] = "0.45"
    values[39] = "22.5"
    values[43] = "2.1"
    values[44] = "18800"
    values[45] = "18800"
    values[46] = "8.8"
    values[47] = "1639.00"
    values[48] = "1341.00"
    values[49] = "1.2"
    values[52] = "23.0"
    return f'v_{code}="' + "~".join(values) + '";'


def test_tencent_code_mapping():
    assert public_symbol_to_tencent("600519.SH") == "sh600519"
    assert public_symbol_to_tencent("300750.SZ") == "sz300750"
    assert public_symbol_to_tencent("700.HK") == "hk00700"
    assert public_symbol_to_tencent("NVDA.US") == "usNVDA"
    assert tencent_code_to_public_symbol("hk00700") == "00700.HK"


def test_parse_tencent_statement_uses_calibrated_fields():
    row = parse_tencent_statement(tencent_line())

    assert row["symbol"] == "600519.SH"
    assert row["name"] == "贵州茅台"
    assert row["last"] == "1500.00"
    assert row["pe_ttm"] == "22.5"
    assert row["pb"] == "8.8"
    assert row["total_mv_yi"] == "18800"


def test_market_public_adapter_fetches_tencent_quote(monkeypatch):
    captured = {}

    def fake_open_url(req, timeout):
        captured["url"] = req.full_url
        return FakeResponse(tencent_line())

    monkeypatch.setenv("MARKET_PUBLIC_PROVIDERS", "tencent")
    monkeypatch.setattr("ir_search.adapters.market_public.open_url", fake_open_url)

    hits = MarketPublicAdapter().query(Query(text="600519 最新行情", count=5))

    assert captured["url"].endswith("sh600519")
    assert hits[0].source == "market_public"
    assert hits[0].evidence_type == EvidenceType.DATA_TABLE
    assert hits[0].extra["requires_token"] is False
    assert hits[0].extra["platform"] == "tencent_quote"


def test_eastmoney_quote_parser_normalizes_scaled_fields():
    row = parse_eastmoney_quote(
        {
            "f58": "贵州茅台",
            "f43": 150000,
            "f60": 149000,
            "f169": 1000,
            "f170": 67,
            "f162": 2250,
            "f167": 880,
            "f116": 1880000000000,
            "f117": 1800000000000,
        },
        "600519.SH",
        "1.600519",
    )

    assert row["provider"] == "eastmoney"
    assert row["last"] == "1500.0"
    assert row["pct_chg"] == "0.67"
    assert row["pe_ttm"] == "22.5"
    assert row["total_mv_yi"] == "18800.0"


def test_baidu_parser_defensively_flattens_nested_payload():
    row = parse_baidu_quote(
        {"Result": {"stockName": "贵州茅台", "latestPrice": "1500.00", "increaseRatio": "0.67%", "increase": "10.00"}},
        "600519.SH",
        "sh600519",
    )

    assert row["provider"] == "baidu"
    assert row["name"] == "贵州茅台"
    assert row["last"] == "1500.00"


def test_market_public_runs_configured_multiple_providers(monkeypatch):
    calls = []

    def fake_tencent(symbols, q):
        calls.append("tencent")
        return [{"provider": "tencent", "platform": "tencent_quote", "symbol": "600519.SH", "name": "贵州茅台", "last": "1500"}]

    def fake_eastmoney(symbols, q):
        calls.append("eastmoney")
        return [{"provider": "eastmoney", "platform": "eastmoney_quote", "symbol": "600519.SH", "name": "贵州茅台", "last": "1501"}]

    monkeypatch.setenv("MARKET_PUBLIC_PROVIDERS", "tencent,eastmoney")
    monkeypatch.setattr("ir_search.adapters.market_public.fetch_tencent_quotes", fake_tencent)
    monkeypatch.setattr("ir_search.adapters.market_public.fetch_eastmoney_quotes", fake_eastmoney)

    hits = MarketPublicAdapter().query(Query(text="600519 最新行情", count=10))

    assert calls == ["tencent", "eastmoney"]
    assert [hit.extra["provider"] for hit in hits] == ["tencent", "eastmoney"]


def test_market_public_keeps_success_when_one_provider_fails(monkeypatch):
    def good(symbols, q):
        return [{"provider": "tencent", "platform": "tencent_quote", "symbol": "600519.SH", "name": "贵州茅台", "last": "1500"}]

    def bad(symbols, q):
        raise RuntimeError("boom")

    monkeypatch.setenv("MARKET_PUBLIC_PROVIDERS", "tencent,eastmoney")
    monkeypatch.setattr("ir_search.adapters.market_public.fetch_tencent_quotes", good)
    monkeypatch.setattr("ir_search.adapters.market_public.fetch_eastmoney_quotes", bad)

    hit = MarketPublicAdapter().query(Query(text="600519 最新行情"))[0]

    assert hit.extra["provider"] == "tencent"
    assert "eastmoney" in hit.extra["provider_errors"]


def test_dataframe_item_value_map_supports_akshare_shape():
    class Frame:
        def to_dict(self, orient):
            assert orient == "records"
            return [{"item": "股票简称", "value": "贵州茅台"}, {"item": "总市值", "value": 1880000000000}]

    assert dataframe_item_value_map(Frame()) == {"股票简称": "贵州茅台", "总市值": 1880000000000}


def test_market_public_source_is_registered():
    registry = build_registry()

    assert registry["market_public"].mode == "live"


def test_market_public_alias_and_route_are_supported():
    q = prepare_query(Query(text="600519", sources=["公共行情"]))

    assert select_sources(q) == ["market_public"]
    assert "market_public" in select_sources(prepare_query(Query(text="腾讯行情 600519")))


def test_market_public_is_market_fallback_target():
    assert fallback_sources_for("tushare") == ["longbridge", "market_public"]
    assert fallback_sources_for("longbridge") == ["market_public"]
