import json

import pytest

from ir_search.adapters.base import AdapterError
from ir_search.adapters.tushare import TushareAdapter, code_to_ts_code, select_apis
from ir_search.kernel import build_registry
from ir_search.models import EvidenceType, Query
from ir_search.pipeline import prepare_query, select_sources


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


def test_tushare_adapter_uses_skill_proxy_http_shape(monkeypatch):
    calls = []

    def fake_open_url(req, timeout, proxy_url=None, disable_proxy=False):
        body = json.loads(req.data.decode("utf-8"))
        calls.append({"url": req.full_url, "body": body, "headers": dict(req.header_items())})
        if body["api_name"] == "stock_basic":
            payload = {"code": 0, "data": {"fields": ["ts_code", "name", "industry"], "items": [["600519.SH", "贵州茅台", "白酒"]]}}
        elif body["api_name"] == "fina_indicator":
            payload = {"code": 0, "data": {"fields": ["ts_code", "ann_date", "eps", "roe"], "items": [["600519.SH", "20260428", 50.0, 32.1]]}}
        else:
            payload = {"code": 0, "data": {"fields": [], "items": []}}
        return FakeResponse(payload)

    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setenv("TUSHARE_PRO_TOKEN", "token-from-env")
    monkeypatch.delenv("TUSHARE_HTTP_URL", raising=False)
    monkeypatch.setenv("TUSHARE_RATE_LIMIT_SECONDS", "0")
    monkeypatch.setattr("ir_search.adapters.tushare.open_url", fake_open_url)

    hits = TushareAdapter().query(Query(text="600519 财务指标", count=5))

    assert [call["url"] for call in calls] == ["https://fastapic.stockai888.top", "https://fastapic.stockai888.top"]
    assert calls[0]["body"]["api_name"] == "stock_basic"
    assert calls[0]["body"]["token"] == "token-from-env"
    assert calls[0]["body"]["params"]["ts_code"] == "600519.SH"
    assert calls[1]["body"]["api_name"] == "fina_indicator"
    assert hits[0].source == "tushare"
    assert hits[0].evidence_type == EvidenceType.DATA_TABLE
    assert hits[0].extra["read_only"] is True


def test_tushare_requires_token(monkeypatch):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.delenv("TUSHARE_PRO_TOKEN", raising=False)

    with pytest.raises(AdapterError, match="TUSHARE_TOKEN"):
        TushareAdapter().query(Query(text="600519 财务指标"))


def test_tushare_code_normalization_includes_bj_market():
    assert code_to_ts_code("600519") == "600519.SH"
    assert code_to_ts_code("300750") == "300750.SZ"
    assert code_to_ts_code("830799") == "830799.BJ"


def test_tushare_api_selection_is_structured_and_read_only():
    assert select_apis("600519 股东人数 龙虎榜 解禁") == ["stock_basic", "stk_holdernumber", "top_list", "share_float"]


def test_tushare_source_is_registered():
    registry = build_registry()

    assert registry["tushare"].mode == "live"


def test_tushare_route_is_explicitly_triggered():
    sources = select_sources(prepare_query(Query(text="600519 股东人数")))

    assert "tushare" in sources


def test_tushare_alias_is_supported():
    q = prepare_query(Query(text="600519", sources=["TUSHARE"]))

    assert select_sources(q) == ["tushare"]
