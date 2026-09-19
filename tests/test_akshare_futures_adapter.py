from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import json
from types import SimpleNamespace
import sys

import pytest

from ir_search import DataRequest, DataRegistry, get_data
from ir_search.adapters.akshare_futures import AKShareFuturesAdapter
from ir_search.adapters.akshare_intraday import AKShareIntradayAdapter
from ir_search.infrastructure.akshare_worker import _execute

NOW = datetime(2026, 9, 14, 8, tzinfo=timezone.utc)


def bar(**kw):
    return dict({"datetime": "2026-09-11 21:05:00", "open": 3000, "high": 3010, "low": 2990,
                 "close": 3001, "volume": 10, "hold": 20}, **kw)


def request(**kw):
    return DataRequest("futures_intraday", symbols=("RB2610",), market="CN_FUTURES", start="2026-09-11", end="2026-09-11", **kw)


def run(rows=None, req=None):
    registry = DataRegistry()
    registry.register(AKShareFuturesAdapter(fetch=lambda *a, **k: rows if rows is not None else [bar()], now=lambda: NOW))
    return get_data(req or request(), registry=registry)


def test_night_session_retains_natural_date_without_inventing_next_trading_day():
    result = run()
    assert result.status.value == "partial" and not result.complete
    row = result.records[0]
    assert str(row["calendar_date"]) == "2026-09-11" and row["trade_date"] is None
    assert row["bar_time"].isoformat() == "2026-09-11T21:05:00+08:00"
    assert row["source_volume"] == Decimal(10) and "volume" not in row
    assert result.provenance.publisher == "Sina via AKShare"
    assert {d.code for d in result.diagnostics} >= {"trade_date_not_resolved", "activity_counting_convention_unverified"}
    json.dumps(result.to_dict(), allow_nan=False)
    assert not run(req=request(allow_partial=False)).records


@pytest.mark.parametrize("change", [dict(symbols=("RB0",)), dict(symbols=("RB2613",)),
    dict(symbols=("IF2609.CFE",)), dict(start="2025-01-01", end="2025-01-01"), dict(frequency="1s"),
    dict(adjustment="forward"), dict(as_of=NOW), dict(cursor="not_a_cursor", provider="akshare")])
def test_invalid_or_ambiguous_contract_requests_do_not_query(change):
    registry = DataRegistry()
    registry.register(AKShareFuturesAdapter(fetch=lambda *a, **k: pytest.fail("unexpected SDK call"), now=lambda: NOW))
    assert not get_data(replace(request(), **change), registry=registry).records


@pytest.mark.parametrize("rows", [[bar(), bar()], [bar(datetime="invalid")], [bar(datetime="2026-09-11Tbad")],
    [bar(high=2980)], [bar(close=5000)], [bar(volume=-1)], [bar(hold=True)], [dict(open=1)]])
def test_corrupt_bars_fail_closed(rows):
    assert run(rows).status.value == "error"
    assert not run(rows).records


def test_empty_nulls_projection_and_limit_are_visible():
    assert not run([]).complete
    result = run([bar(volume=float("nan"))])
    assert result.records[0]["source_volume"] is None
    assert "missing_source_values" in [d.code for d in result.diagnostics]
    projected = run(req=request(fields=("close",)))
    assert set(projected.records[0]) == {"symbol", "bar_time", "calendar_date", "trade_date", "trade_date_source", "currency", "close"}
    limited = run([bar(), bar(datetime="2026-09-11 21:10:00")], request(limit=1))
    assert len(limited.records) == 1 and not limited.next_cursor
    assert "row_limit_reached_narrow_request" in [d.code for d in limited.diagnostics]


def test_future_bar_end_label_within_one_period_is_excluded_without_losing_closed_bars():
    registry = DataRegistry()
    registry.register(AKShareFuturesAdapter(fetch=lambda *a, **kw:[bar(datetime='2026-09-14 13:00:00'),
        bar(datetime='2026-09-14 13:05:00')],now=lambda:datetime(2026,9,14,5,2,tzinfo=timezone.utc)))
    req=replace(request(), start='2026-09-14',end='2026-09-14',frequency='5m')
    result=get_data(req,registry=registry)
    assert len(result.records)==1 and result.records[0]['bar_time'].minute==0
    assert 'unclosed_bar_excluded' in {d.code for d in result.diagnostics}


def test_sina_stock_backend_exposes_price_only_capability_and_never_scales_unknown_activity():
    calls = []
    def fetch(name, kwargs, **other):
        calls.append((name, kwargs))
        return [{"day": "2026-09-11 15:00:00", "open": "10", "high": "12", "low": "9", "close": "11", "volume": 123}]
    registry = DataRegistry()
    registry.register(AKShareIntradayAdapter(backend="sina", fetch=fetch, now=lambda: NOW))
    req = DataRequest("prices_intraday", symbols=("600519.SH",), start="2026-09-11", end="2026-09-11", fields=("close",))
    result = get_data(req, registry=registry)
    assert result.records[0]["close"] == 11 and "volume" not in result.records[0]
    assert result.provenance.publisher == "Sina via AKShare"
    assert calls[0] == ("stock_zh_a_minute", {"symbol": "sh600519", "period": "1", "adjust": ""})
    calls.clear()
    assert not get_data(replace(req, fields=("volume",)), registry=registry).records
    assert not calls
    with pytest.raises(ValueError):
        AKShareIntradayAdapter(backend="unknown")


@pytest.mark.parametrize("function", ["stock_zh_a_minute", "futures_zh_minute_sina"])
def test_new_worker_functions_are_bounded_and_logs_suppressed(monkeypatch, capsys, function):
    class Frame:
        def __len__(self): return 1
        def to_json(self, **kw): return '[{"close":1}]'
    def sdk(**kw):
        print("private sentinel")
        return Frame()
    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(**{function: sdk}))
    assert _execute({"function": function, "kwargs": {}}) == {"records": [{"close": 1}]}
    assert not capsys.readouterr().out
