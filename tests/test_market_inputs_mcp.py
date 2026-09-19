"""A skill-like caller gets a numeric page and citeable material through MCP payloads."""
from datetime import datetime
from decimal import Decimal
import json

from ir_search.mcp_server import get_data_payload, search_announcements_payload, retrieve_payload


def test_market_research_inputs_share_public_interfaces_without_exposing_credentials(tmp_path, monkeypatch):
    path = tmp_path / "sources.env"
    lines = []
    for prefix in ("WIND_MYSQL", "JYDB_MYSQL"):
        for key, value in {"ENABLED":"true", "HOST":"host", "DATABASE":"db", "USER":"user", "PASSWORD":"must_not_escape"}.items():
            lines.append(f"{prefix}_{key}={value}")
    lines.extend(["WIND_MYSQL_VOLUME_MULTIPLIER=100", "WIND_MYSQL_AMOUNT_MULTIPLIER=1000"])
    path.write_text("\n".join(lines))
    path.chmod(0o600)
    monkeypatch.setenv("IR_SEARCH_CREDENTIALS_FILE", str(path))

    def wind_select(profile, sql, params, **kwargs):
        return [{"symbol":"000001.SZ", "trade_date":"20260902", "close":Decimal("11.2"), "currency":"CNY"}]

    def jydb_select(profile, sql, params, **kwargs):
        if "DISTINCT ChiName" in sql:
            return [{"ChiName":"fixture issuer"}]
        if "FROM SecuMain" in sql:
            return [{"InnerCode":1, "SecuCode":"000001", "SecuMarket":90, "CompanyCode":7}]
        row = {"ID":101, "CompanyCode":7, "InfoPublDate":datetime(2026,9,2), "InfoTitle":"半年报", "Category":6, "Media":"fixture"}
        if "LEFT(Content" in sql:
            row["Content"] = "公司收入增长，现金流改善。"
        return [row]

    monkeypatch.setattr("ir_search.adapters.wind_mysql._select", wind_select)
    monkeypatch.setattr("ir_search.adapters.jydb._select", jydb_select)
    data = get_data_payload({"dataset":"prices_daily", "provider":"wind_mysql", "symbols":["000001.SZ"],
                             "start":"2026-09-01", "end":"2026-09-03", "fields":["close"]})
    discovery = search_announcements_payload(["000001.SZ"], "2026-09-01", "2026-09-03")
    material = retrieve_payload("收入", [item["source_ref"] for item in discovery["items"]])
    assert data["status"] == "ok" and data["records"][0]["close"] == "11.2"
    assert discovery["complete"] and material["materials"][0]["evidence_spans"]
    assert material["materials"][0]["provenance"]["provider"] == "jydb"
    assert "must_not_escape" not in json.dumps([data, discovery, material], allow_nan=False)


def test_mcp_default_wind_empty_uses_jydb_and_reports_fallback(tmp_path, monkeypatch):
    path = tmp_path / "sources.env"
    lines = []
    for prefix in ("WIND_MYSQL", "JYDB_MYSQL"):
        for key, value in {"ENABLED":"true", "HOST":"host", "DATABASE":"db", "USER":"user", "PASSWORD":"must_not_escape"}.items():
            lines.append(f"{prefix}_{key}={value}")
    path.write_text("\n".join(lines))
    path.chmod(0o600)
    monkeypatch.setenv("IR_SEARCH_CREDENTIALS_FILE", str(path))
    monkeypatch.setattr("ir_search.adapters.wind_mysql._select", lambda *a, **kw: [])
    def jydb_select(profile, sql, params, **kwargs):
        if "FROM SecuMain" in sql:
            return [{"InnerCode":1, "SecuCode":"000001", "SecuMarket":90, "ListedSector":1}]
        return [{"InnerCode":1, "trade_date":datetime(2026,9,2), "close":Decimal("11.2")}]
    monkeypatch.setattr("ir_search.adapters.jydb_market._select", jydb_select)
    result = get_data_payload({"dataset":"prices_daily", "symbols":["000001.SZ"], "fields":["close"],
                               "start":"2026-09-01", "end":"2026-09-03"})
    assert result["status"] == "ok" and result["provenance"]["provider"] == "jydb"
    assert "fallback_used" in [d["code"] for d in result["diagnostics"]]
    assert "must_not_escape" not in json.dumps(result)


def test_mcp_intraday_default_frequency_and_diagnostics(tmp_path, monkeypatch):
    from datetime import timedelta, timezone
    from zoneinfo import ZoneInfo
    path = tmp_path / "sources.env"
    path.write_text("AKSHARE_ENABLED=true\n")
    path.chmod(0o600)
    monkeypatch.setenv("IR_SEARCH_CREDENTIALS_FILE", str(path))
    day = datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)
    def fetch(function, kwargs, *, context):
        assert kwargs["period"] == "1"
        return [{"时间":f"{day} 10:00:00", "开盘":10, "最高":11, "最低":9,
                 "收盘":10, "成交量":2, "成交额":2000}]
    monkeypatch.setattr("ir_search.adapters.akshare_intraday._fetch", fetch)
    result = get_data_payload({"dataset":"prices_intraday", "symbols":["000001.SZ"], "start":str(day), "end":str(day)})
    assert result["status"] == "partial" and result["provenance"]["provider"] == "akshare"
    assert result["records"][0]["volume"] == "200" and not result["complete"]
    assert "recent_intraday_coverage_unverified" in [d["code"] for d in result["diagnostics"]]
