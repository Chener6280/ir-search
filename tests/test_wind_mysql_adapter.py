from dataclasses import replace
from decimal import Decimal
import json

import pytest

from ir_search import DataRegistry, DataRequest, RequestContext, get_data
from ir_search.adapters.wind_mysql import WindMySQLAdapter
from ir_search.infrastructure.credentials import MySQLProfile

PROFILE = MySQLProfile("wind_mysql", "host", "db", "user", "must_not_escape",
                       volume_multiplier=Decimal(100), amount_multiplier=Decimal(1000))


def price(day="20260901"):
    return dict(symbol="000001.SZ", trade_date=day, open=10, high=12, low=9, close=Decimal("11.2"),
                volume=Decimal("12.3"), amount=Decimal("2.4"), currency="CNY")


def request(**kwargs):
    return DataRequest("prices_daily", symbols=["000001.SZ"], start="2026-09-01", end="2026-09-03", **kwargs)


def run(rows, req=None, profile=PROFILE):
    calls = []
    def select(profile, sql, params, **kw):
        calls.append((sql, params, kw))
        return rows
    adapter = WindMySQLAdapter(profile, select=select)
    registry = DataRegistry()
    registry.register(adapter)
    return get_data(req or request(), registry=registry), calls


def test_wind_preserves_dates_converts_units_and_reports_data_vendor():
    result, calls = run([price(), price("20260902")])
    assert result.status.value == "ok" and result.complete and len(result.records) == 2
    assert result.records[0]["volume"] == Decimal("1230")
    assert result.records[0]["amount"] == Decimal("2400")
    assert result.provenance.authority.value == "data_vendor"
    assert calls[0][1] == ("000001.SZ", "20260901", "20260903", 1001)
    assert "000001" not in calls[0][0] and "JOIN" not in calls[0][0]
    assert "must_not_escape" not in json.dumps(result.to_dict())


def test_unknown_units_are_not_guessed_but_price_projection_still_works():
    profile = replace(PROFILE, volume_multiplier=None, amount_multiplier=None)
    result, calls = run([], profile=profile)
    assert result.diagnostics[-1].code == "units_not_configured" and not calls
    row = {k:v for k,v in price().items() if k in {"symbol", "trade_date", "close", "currency"}}
    result, calls = run([row], request(fields=["close"]), profile)
    assert result.status.value == "ok" and "S_DQ_VOLUME" not in calls[0][0]


def test_wind_cursor_resumes_without_duplicate_and_is_bound_to_request():
    first, _ = run([price(), price("20260902")], request(limit=1))
    assert first.status.value == "partial" and first.next_cursor and len(first.records) == 1
    second_request = request(limit=1, provider="wind_mysql", cursor=first.next_cursor)
    second, calls = run([price("20260902")], second_request)
    assert second.complete and calls[0][1][-3:] == ("000001.SZ", "20260901", 2)
    changed, calls = run([], replace(second_request, end="2026-09-02"))
    assert changed.diagnostics[-1].code == "invalid_cursor" and not calls
    changed, calls = run([], replace(second_request, cursor=first.next_cursor + "x"))
    assert changed.diagnostics[-1].code == "invalid_cursor" and not calls


def test_wind_directory_returns_standard_market_and_has_no_date_filter():
    row = {"symbol":"000001.SZ", "name":"fixture", "exchange":"SZSE", "currency":"CNY"}
    result, calls = run([row], DataRequest("securities", symbols=["000001.SZ"]))
    assert result.records[0]["market"] == "A_SHARE"
    assert "TRADE_DT" not in calls[0][0] and "asharedescription" in calls[0][0]


@pytest.mark.parametrize("req", [
    DataRequest("prices_daily", symbols=["bad' OR 1=1"], start="2026-09-01", end="2026-09-03"),
    request(adjustment="forward"), request(as_of="2026-09-03T00:00:00Z"), request(market="HK"),
])
def test_unsupported_wind_inputs_do_not_query_database(req):
    result, calls = run([], req)
    assert result.status.value in {"error", "unavailable"} and not calls


@pytest.mark.parametrize("row", [dict(price(), close=float("nan")), dict(price(), volume=True), dict(price(), currency=None),
                                  dict(price(), symbol="600519.SH"), dict(price(), trade_date="20260801")])
def test_bad_wind_rows_never_escape_as_success(row):
    result, _ = run([row])
    assert result.status.value == "error" and not result.records
