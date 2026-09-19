from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from ir_search import DataRequest, DataRegistry, RequestContext, build_data_registry, get_data, source_configuration_status
from ir_search.adapters.akshare_intraday import AKShareIntradayAdapter, _fetch
from ir_search.infrastructure.akshare_worker import _execute
from ir_search.registry import DataAdapterError
from ir_search.context import RequestStopped

NOW = datetime(2026, 9, 13, 8, tzinfo=timezone.utc)


def request(**kw):
    return DataRequest("prices_intraday", symbols=["000001.SZ"], start="2026-09-10", end="2026-09-11", **kw)


def bar(day=11, minute=31, **kw):
    return dict({"时间": f"2026-09-{day:02d} 09:{minute:02d}:00", "开盘": 10, "最高": 11,
                 "最低": 9, "收盘": 10.5, "成交量": 2, "成交额": 2100}, **kw)


class Source:
    def __init__(self, rows=None):
        self.rows = rows if rows is not None else [bar()]
        self.calls = []

    def fetch(self, function, kwargs, *, context):
        self.calls.append((function, kwargs))
        return self.rows

    def run(self, req=None):
        registry = DataRegistry()
        registry.register(AKShareIntradayAdapter(fetch=self.fetch, now=lambda: NOW))
        return get_data(req or request(), registry=registry)


@pytest.mark.parametrize("frequency", ["1m", "5m", "15m", "30m", "60m"])
def test_recent_bar_contract_timezone_units_and_source_provenance(frequency):
    source = Source([bar(10), bar(11)])
    result = source.run(request(frequency=frequency))
    assert result.status.value == "partial" and not result.complete and len(result.records) == 2
    row = result.records[0]
    assert row["volume"] == Decimal(200) and row["amount"] == Decimal(2100) and row["currency"] == "CNY"
    assert row["bar_time"].utcoffset() == timedelta(hours=8)
    assert row["trade_date"].isoformat() == "2026-09-10"
    assert source.calls[0][0] == "stock_zh_a_hist_min_em"
    assert source.calls[0][1]["period"] == frequency[:-1] and source.calls[0][1]["adjust"] == ""
    assert result.provenance.provider == "akshare" and result.provenance.publisher == "Eastmoney via AKShare"
    json.dumps(result.to_dict(), allow_nan=False)


def test_intraday_default_frequency_and_coverage_metadata_validate():
    assert request().frequency == "1m" and request().adjustment == "raw"
    cap = AKShareIntradayAdapter().capabilities[0]
    assert cap.coverage_notes and not cap.supports_pagination and not cap.supports_as_of
    with pytest.raises(ValueError):
        replace(cap, coverage_notes="untyped string")


def test_zero_price_nulls_nan_volume_zero_and_strict_partial_handling():
    source = Source([bar(**{"开盘": 0, "成交量": 0, "成交额": float("nan")})])
    result = source.run()
    assert result.records[0]["open"] is None and result.records[0]["amount"] is None
    assert result.records[0]["volume"] == 0
    assert "zero_source_price_replaced_with_null" in [d.code for d in result.diagnostics]
    assert not source.run(request(allow_partial=False)).records
    projected = source.run(request(fields=["close"]))
    assert set(projected.records[0]) == {"symbol", "bar_time", "trade_date", "close", "currency"}


def test_recent_window_filter_limits_and_empty_results_never_claim_complete():
    source = Source([bar(9), bar(10), bar(11)])
    result = source.run(request(limit=1))
    assert len(result.records) == 1 and result.records[0]["trade_date"].day == 10 and not result.next_cursor
    assert "row_limit_reached_narrow_request" in [d.code for d in result.diagnostics]
    empty = Source([]).run()
    assert empty.status.value == "partial" and not empty.complete and not empty.records
    assert "no_intraday_rows_for_requested_symbol" in [d.code for d in empty.diagnostics]


def test_forming_bar_is_excluded_and_older_window_is_explicit():
    registry=DataRegistry()
    registry.register(AKShareIntradayAdapter(fetch=lambda *a,**k:[bar(minute=31),bar(minute=32)],
        now=lambda:datetime(2026,9,11,1,31,30,tzinfo=timezone.utc)))
    result=get_data(replace(request(),start='2026-09-11'),registry=registry)
    assert len(result.records)==1 and 'unclosed_bar_excluded' in {d.code for d in result.diagnostics}
    result=Source([bar(10)]).run()
    assert 'requested_end_date_without_bars' in {d.code for d in result.diagnostics}


@pytest.mark.parametrize("req", [
    replace(request(), start="2026-08-01", end="2026-08-02"),
    replace(request(), start="2026-09-14", end="2026-09-14"),
    request(frequency="1s"), request(adjustment="forward"), request(as_of=NOW),
    replace(request(), symbols=["920001.BJ"]), replace(request(), symbols=["100000.SH"]),
    request(market="CN_FUTURES"), request(provider="akshare", cursor="cursor"),
])
def test_unsupported_intraday_requests_never_reach_sdk(req):
    source = Source()
    assert not source.run(req).records and not source.calls


@pytest.mark.parametrize("rows", [
    [bar(), bar()], [bar(**{"时间": "unknown"})], [bar(**{"收盘": "invalid"})],
    [bar(**{"成交量": -1})], [bar(**{"成交量": True})], [{"时间":"2026-09-11 09:31:00"}],
])
def test_malformed_upstream_rows_are_never_silently_accepted(rows):
    result = Source(rows).run()
    assert result.status.value == "error" and not result.records


def test_sdk_timeout_terminates_call_and_does_not_forward_credentials(monkeypatch):
    monkeypatch.setenv("PRIVATE_API_KEY", "must_not_escape")
    original = subprocess.Popen
    children = []
    def popen(*args, **kwargs):
        assert "PRIVATE_API_KEY" not in kwargs["env"]
        assert kwargs["stderr"] == subprocess.PIPE
        process = original([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
        children.append(process)
        return process
    monkeypatch.setattr("ir_search.adapters.akshare_intraday.subprocess.Popen", popen)
    with pytest.raises(RequestStopped, match="deadline_exceeded"):
        _fetch("stock_zh_a_hist_min_em", {}, context=RequestContext(timeout_seconds=0.15))
    assert children[0].poll() is not None


@pytest.mark.parametrize("payload,expected", [("invalid must_not_escape", "upstream_schema"),
                                               ('{"error":"dependency_missing"}', "dependency_missing"),
                                               ('{"error":"must_not_escape"}', "upstream_schema")])
def test_worker_output_and_errors_are_sanitized(monkeypatch, payload, expected):
    original = subprocess.Popen
    def popen(*args, **kwargs):
        return original([sys.executable, '-c', 'import sys; sys.stdout.write('+repr(payload)+')'], **kwargs)
    monkeypatch.setattr("ir_search.adapters.akshare_intraday.subprocess.Popen", popen)
    with pytest.raises(DataAdapterError, match=expected) as caught:
        _fetch("stock_zh_a_hist_min_em", {}, context=RequestContext())
    assert "must_not_escape" not in str(caught.value)


def test_worker_cancellation_reaps_process_and_next_request_recovers(monkeypatch):
    import threading
    original = subprocess.Popen
    children = []
    def popen(*args, **kwargs):
        code = 'import time; time.sleep(30)' if not children else 'print(\'{"records": []}\')'
        process = original([sys.executable, '-c', code], **kwargs)
        children.append(process)
        return process
    monkeypatch.setattr("ir_search.adapters.akshare_intraday.subprocess.Popen", popen)
    context = RequestContext(timeout_seconds=10)
    timer = threading.Timer(0.15, context.cancel)
    timer.start()
    try:
        with pytest.raises(RequestStopped, match='cancelled'):
            _fetch('stock_zh_a_minute', {}, context=context)
    finally:
        timer.join()
    assert children[0].poll() is not None
    assert _fetch('stock_zh_a_minute', {}, context=RequestContext()) == []


def test_worker_suppresses_sdk_logs_and_returns_bounded_structured_data(monkeypatch, capsys):
    class Frame:
        def __len__(self):
            return 1
        def to_json(self, **kw):
            return json.dumps([bar()])
    def sdk(**kw):
        print("must_not_escape")
        return Frame()
    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(stock_zh_a_hist_min_em=sdk))
    assert _execute({"function":"stock_zh_a_hist_min_em", "kwargs":{}})["records"] == [bar()]
    assert "must_not_escape" not in capsys.readouterr().out
    assert _execute({"function":"unapproved", "kwargs":{}})["error"] == "unsupported"
    monkeypatch.setitem(sys.modules, "akshare", None)
    assert _execute({"function":"stock_zh_a_hist_min_em", "kwargs":{}})["error"] == "dependency_missing"


def test_registry_configuration_is_lazy_and_safe(tmp_path, monkeypatch):
    path = tmp_path / "credentials.env"
    path.write_text("AKSHARE_ENABLED=true\n")
    path.chmod(0o600)
    monkeypatch.setitem(sys.modules, "akshare", None)
    registry = build_data_registry(env_file=path)
    assert {cap.dataset for cap, _ in registry.entries()} == {'prices_intraday','futures_intraday','options_intraday'}
    status = next(s for s in source_configuration_status(env_file=path)["sources"] if s["provider"] == "akshare")
    assert status["enabled"] and not status["requires_key"] and not status["live_verified"]
    path.write_text("AKSHARE_ENABLED=invalid\n")
    assert not build_data_registry(env_file=path).entries()
    assert next(s for s in source_configuration_status(env_file=path)["sources"] if s["provider"] == "akshare")["code"] == "source_config_error"
