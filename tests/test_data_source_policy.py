from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import json

import pytest

from ir_search import DataRequest, DataRegistry, DataAdapterError, Diagnostic, get_data, list_capabilities, source_policy
from ir_search.models import FailureKind
from ir_search.source_policy import SourceRoute
from test_framework_data import Upstream


def request(**kwargs):
    return DataRequest("prices_daily", symbols=["000001.SZ"], start="2026-09-01", end="2026-09-02", **kwargs)


def registry(*sources):
    value = DataRegistry(use_source_policy=True)
    for source in sources:
        value.register(source)
    return value


def codes(result):
    return [item.code for item in result.diagnostics]


def test_policy_separates_planned_routes_from_registered_capabilities():
    policy = source_policy()
    assert policy["historical_intraday_provider"] is None
    assert SourceRoute("prices_daily", "A_SHARE", ("wind_mysql", "jydb")).to_dict()["providers"] == ["wind_mysql", "jydb"]
    assert {r["dataset"] for r in policy["routes"]} >= {"financial_statements", "futures_daily", "options_daily"}
    assert all(r["providers"] == ["wind_mysql", "jydb"] for r in policy["routes"] if r["dataset"] in {"securities", "prices_daily", "financial_statements", "futures_daily", "options_daily", "futures_contracts", "options_contracts", "derivatives_daily"} and r["market"] != "US")
    assert list_capabilities(registry=registry())["capabilities"] == []
    assert list_capabilities(registry=registry())["source_policy"] == policy
    assert list_capabilities(registry=DataRegistry())["source_policy"] is None
    json.dumps(policy)
    with pytest.raises(ValueError):
        DataRegistry(use_source_policy="yes")
    result = get_data(DataRequest("derivatives_daily", market="CN_DERIVATIVES"), registry=registry())
    assert result.status.value == "unavailable" and "dataset_mapping_not_implemented" in codes(result)


def test_wind_precedes_alphabetically_earlier_jydb_and_empty_wind_falls_back():
    wind, jydb = Upstream("wind_mysql"), Upstream("jydb")
    sources = registry(jydb, wind)
    result = get_data(request(), registry=sources)
    assert result.provenance.provider == "wind_mysql" and not jydb.calls
    wind.page.records = []
    result = get_data(request(), registry=sources)
    assert result.status.value == "ok" and result.provenance.provider == "jydb"
    assert "no_rows" in codes(result) and "fallback_used" in codes(result)
    assert [d.provider for d in result.diagnostics if d.code == "provider_attempted"] == ["wind_mysql", "jydb"]
    assert result.provenance.adapter_mode.value == "live"


def test_unregistered_or_unsupported_wind_can_use_jydb_but_explicit_provider_never_switches():
    wind, jydb = Upstream("wind_mysql"), Upstream("jydb")
    result = get_data(request(), registry=registry(jydb))
    assert result.provenance.provider == "jydb" and "primary_provider_not_registered" in codes(result)
    wind.capabilities = (replace(wind.capabilities[0], fields=("symbol", "trade_date", "currency", "close")),)
    result = get_data(request(), registry=registry(wind, jydb))
    assert not wind.calls and result.provenance.provider == "jydb" and "fields_not_supported" in codes(result)
    jydb.calls.clear()
    result = get_data(request(provider="wind_mysql"), registry=registry(wind, jydb))
    assert result.status.value == "unavailable" and not jydb.calls


@pytest.mark.parametrize("code", ["unsupported", "not_found"])
def test_typed_missing_coverage_allows_fallback(code):
    wind, jydb = Upstream("wind_mysql"), Upstream("jydb")
    wind.error = DataAdapterError(code)
    result = get_data(request(), registry=registry(wind, jydb))
    assert result.provenance.provider == "jydb" and code in codes(result)


@pytest.mark.parametrize("code", ["network", "timeout", "tls_error", "no_credential", "upstream_schema", "rate_limit", "units_not_configured"])
def test_operational_errors_are_not_mistaken_for_missing_coverage(code):
    wind, jydb = Upstream("wind_mysql"), Upstream("jydb")
    wind.error = DataAdapterError(code)
    result = get_data(request(), registry=registry(wind, jydb))
    assert result.status.value == "error" and not jydb.calls and codes(result)[-1] == code


def test_broken_primary_configuration_and_denied_access_cannot_trigger_fallback():
    jydb = Upstream("jydb")
    sources = registry(jydb)
    sources.diagnostics.append(Diagnostic("source_credentials_missing", "configure", provider="wind_mysql"))
    assert get_data(request(), registry=sources).status.value == "error" and not jydb.calls
    wind = Upstream("wind_mysql")
    wind.capabilities = (replace(wind.capabilities[0], access="denied"),)
    assert get_data(request(), registry=registry(wind, jydb)).status.value == "unavailable" and not jydb.calls


def test_pagination_partial_failure_and_empty_explicit_provider_do_not_switch():
    wind, jydb = Upstream("wind_mysql", supports_pagination=True), Upstream("jydb")
    wind.page.complete = False
    wind.page.next_cursor = "next"
    result = get_data(request(), registry=registry(wind, jydb))
    assert result.status.value == "partial" and result.next_cursor == "next" and not jydb.calls
    wind.page.records, wind.page.next_cursor, wind.page.complete = [], None, True
    result = get_data(request(provider="wind_mysql"), registry=registry(wind, jydb))
    assert result.complete and not result.records and not jydb.calls
    wind.page.diagnostics = [Diagnostic("network", "query_data", failure_kind=FailureKind.NETWORK)]
    result = get_data(request(), registry=registry(wind, jydb))
    assert result.status.value == "partial" and not jydb.calls


def test_no_fallback_to_unapproved_market_source_and_history_is_intraday_only():
    alternative = Upstream("tushare")
    result = get_data(request(), registry=registry(alternative))
    assert result.status.value == "unavailable" and not alternative.calls
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    old = today - timedelta(days=40)
    intraday = DataRequest("prices_intraday", symbols=["000001.SZ"], start=old, end=old)
    assert "historical_intraday_source_not_configured" in codes(get_data(intraday, registry=registry()))
    assert "historical_intraday_source_not_configured" not in codes(get_data(request(), registry=registry()))
    future = replace(intraday, start=today + timedelta(days=1), end=today + timedelta(days=1))
    assert "future_intraday_not_supported" in codes(get_data(future, registry=registry()))
