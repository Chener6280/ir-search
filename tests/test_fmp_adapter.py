"""Offline FMP contract, normalization and SDK/MCP integration tests."""
import asyncio
import copy
import json
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from ir_search import DataRequest, DataRegistry, get_data, list_capabilities, describe_dataset
from ir_search import mcp_server
from ir_search.adapters.fmp import FMPAdapter
from ir_search.context import RequestContext
from ir_search.infrastructure.credentials import FMPProfile, SourceConfigError, fmp_profile, source_configuration_status
from ir_search.infrastructure.fmp import _Response
from ir_search.registry import build_data_registry

NOW = datetime(2026, 9, 15, tzinfo=timezone.utc)
KEY = "test_only_fmp_key"


def profile_row(symbol="AAPL"):
    return dict(symbol=symbol, companyName="Apple Inc.", exchange="NASDAQ", currency="USD", isEtf=False, isFund=False)


def financial_row(**changes):
    return dict(dict(symbol="AAPL", date="2025-09-27", fiscalYear="2025", period="FY", reportedCurrency="USD",
                     filingDate="2025-10-31", acceptedDate="2025-10-31 06:01:26", revenue=100,
                     operatingIncome=30, netIncome=20, totalAssets=80, totalLiabilities=60,
                     totalStockholdersEquity=20, totalEquity=20, operatingCashFlow=40,
                     netCashProvidedByInvestingActivities=-10, netCashProvidedByFinancingActivities=-20,
                     capitalExpenditure=-10, freeCashFlow=30), **changes)


class Client:
    def __init__(self):
        self.calls = []
        self.rows = {"profile": [profile_row()], "historical-price-eod/light": [
            dict(symbol="AAPL", date="2026-09-14", price=Decimal("237.1200"), volume=100)]}
        self.rows.update({endpoint: [financial_row()] for endpoint in
                          ("income-statement", "balance-sheet-statement", "cash-flow-statement")})
        self.error = None

    def fetch(self, endpoint, params, *, context, budget):
        budget.consume()
        context.begin_operation()
        self.calls.append((endpoint, params))
        if self.error:
            raise self.error
        return _Response(copy.deepcopy(self.rows[endpoint]), NOW)


def setup(**settings):
    client = Client()
    adapter = FMPAdapter(FMPProfile(KEY, **settings), client=client, now=lambda: NOW)
    registry = DataRegistry(use_source_policy=True)
    registry.register(adapter)
    return registry, client, adapter


def request(dataset="prices_daily_basic", **kwargs):
    values = dict(market="US", symbols=["AAPL"])
    if dataset != "securities":
        values.update(start="2024-01-01" if dataset.startswith("financial") else "2026-09-07", end="2026-09-14")
    values.update(kwargs)
    return DataRequest(dataset, **values)


def codes(result):
    return {d.code for d in result.diagnostics}


def test_configuration_discovery_no_probe_or_secret(tmp_path):
    assert fmp_profile(values={}) is None
    assert fmp_profile(values={"FMP_API_KEY": KEY}) is None
    assert KEY not in repr(FMPProfile(KEY))
    target = tmp_path / "credentials.env"
    target.write_text("FMP_ENABLED=true\nFMP_API_KEY=" + KEY + "\n")
    target.chmod(0o600)
    config = source_configuration_status(env_file=target)
    item = next(s for s in config["sources"] if s["provider"] == "fmp")
    assert item["configured"] and not item["live_verified"] and item["access"] == "unknown"
    registry = build_data_registry(env_file=target)
    catalog = list_capabilities(registry=registry)
    assert len(catalog["capabilities"]) == 3
    assert all(c["provider"] == "fmp" and c["adapter_mode"] == "live" for c in catalog["capabilities"])
    assert KEY not in json.dumps([config, catalog])
    assert describe_dataset("financial_statements_standardized", registry=registry)["status"] == "ok"
    assert describe_dataset("prices_daily_basic", registry=registry)["definition"]["primary_key"] == ["symbol", "trade_date"]
    assert fmp_profile(env_file=target).max_requests_per_query == 5


@pytest.mark.parametrize("values", [{"FMP_ENABLED": "maybe"}, {"FMP_ENABLED": "true"},
    {"FMP_ENABLED": "true", "FMP_API_KEY": "demo"}, {"FMP_ENABLED": "true", "FMP_API_KEY": "YOUR_API_KEY"},
    {"FMP_ENABLED": "true", "FMP_API_KEY": KEY, "FMP_MAX_REQUESTS_PER_QUERY": "26"},
    {"FMP_ENABLED": "true", "FMP_API_KEY": KEY, "FMP_ANNUAL_RECORD_LIMIT": "0"},
    {"FMP_ENABLED": "true", "FMP_API_KEY": KEY, "FMP_CACHE_TTL_SECONDS": "nan"}])
def test_bad_config_typed_and_secret_safe(values, tmp_path):
    with pytest.raises(SourceConfigError) as caught:
        fmp_profile(values=values)
    assert KEY not in str(caught.value)
    target = tmp_path / "bad.env"
    target.write_text("\n".join(k + "=" + v for k, v in values.items()))
    target.chmod(0o600)
    assert build_data_registry(env_file=target).diagnostics
    assert not next(s for s in source_configuration_status(env_file=target)["sources"] if s["provider"] == "fmp")["configured"]


def test_profile_and_defaults_and_projection():
    registry, client, _ = setup()
    result = get_data(request("securities"), registry=registry)
    assert result.status.value == "ok" and result.complete
    assert result.records[0]["exchange"] == "NASDAQ"
    assert result.provenance.authority.value == "data_vendor" and result.provenance.source_tier is None
    assert result.provenance.evidence_type.value == "data_table" and not result.provenance.generated
    assert len(client.calls) == 1
    assert request().adjustment == "source_unspecified"
    assert request("financial_statements_standardized").frequency == "annual"
    assert request("financial_statements_standardized").adjustment == "none"
    result = get_data(request("securities", fields=["name"]), registry=registry)
    assert set(result.records[0]) == {"symbol", "name", "currency"}
    with pytest.raises(ValueError):
        FMPAdapter(None)


def test_daily_does_not_invent_ohlc_units_or_completeness():
    registry, client, _ = setup()
    result = get_data(request(), registry=registry)
    assert result.status.value == "partial" and not result.complete
    assert result.records[0]["price"] == Decimal("237.1200")
    assert result.records[0]["source_volume"] == 100
    assert "close" not in result.records[0]
    assert {"daily_price_adjustment_unverified", "volume_convention_unverified"} <= codes(result)
    assert result.provenance.fetched_at == NOW
    assert len(client.calls) == 2
    strict = get_data(request(allow_partial=False), registry=registry)
    assert strict.status.value == "unavailable" and not strict.records
    projected = get_data(request(fields=["price"]), registry=registry)
    assert set(projected.records[0]) == {"symbol", "trade_date", "price", "currency"}
    json.dumps(result.to_dict(), allow_nan=False)


def test_financial_statement_selection_currency_fiscal_dates_and_version_stability():
    registry, client, _ = setup()
    client.rows["income-statement"][0]["reportedCurrency"] = "EUR"
    all_result = get_data(request("financial_statements_standardized"), registry=registry)
    assert all_result.status.value == "partial" and len(all_result.records) == 3
    assert len(client.calls) == 4
    income = next(r for r in all_result.records if r["statement"] == "income")
    assert income["report_period"].isoformat() == "2025-09-27" and income["currency"] == "EUR"
    assert income["total_assets"] is None and income["net_income"] == 20
    assert income["accepted_at_source"] == "2025-10-31 06:01:26"
    assert income["version_basis"] == "provider_current_snapshot" and income["version_id"].startswith("sha256:")
    client.calls.clear()
    partial = get_data(request("financial_statements_standardized", fields=["revenue"]), registry=registry)
    assert len(partial.records) == 1 and len(client.calls) == 2
    assert partial.records[0]["version_id"] == income["version_id"]
    assert "net_income" not in partial.records[0]
    client.rows["income-statement"][0]["revenue"] = Decimal("100.000")
    assert get_data(request("financial_statements_standardized", fields=["revenue"]), registry=registry).records[0]["version_id"] == income["version_id"]
    client.rows["income-statement"][0]["netIncome"] = 21
    assert get_data(request("financial_statements_standardized", fields=["revenue"]), registry=registry).records[0]["version_id"] != income["version_id"]


def test_financial_missing_field_period_filter_metadata_only_and_limit():
    registry, client, _ = setup(annual_record_limit=2)
    client.rows["income-statement"][0].pop("revenue")
    response = get_data(request("financial_statements_standardized", fields=["revenue"]), registry=registry)
    assert response.records[0]["revenue"] is None and "source_field_missing" in codes(response)
    assert client.calls[-1][1]["limit"] == 2
    response = get_data(request("financial_statements_standardized", fields=["fiscal_year"], limit=1), registry=registry)
    assert len(response.records) == 1 and "row_limit_reached" in codes(response)
    response = get_data(request("financial_statements_standardized", start="2020-01-01", end="2020-12-31"), registry=registry)
    assert response.status.value == "partial" and not response.records
    assert {"annual_history_may_be_truncated", "requested_statement_window_empty"} <= codes(response)


@pytest.mark.parametrize("kwargs", [{"market": "HK"}, {"adjustment": "raw"}, {"symbols": []},
    {"symbols": ["AAPL&apikey=x"]}, {"start": "2020-01-01"}, {"end": "2099-01-01"},
    {"frequency": "1m"}, {"as_of": "2025-01-01T00:00:00Z"}, {"cursor": "made-up", "provider": "fmp"}])
def test_unsupported_requests_do_not_spend_quota(kwargs):
    registry, client, _ = setup()
    result = get_data(request(**kwargs), registry=registry)
    assert not result.records and not client.calls


def test_request_budget_preflight_and_cancellation():
    registry, client, adapter = setup()
    result = get_data(request(symbols=["AAPL", "MSFT", "NVDA"]), registry=registry)
    assert "fmp_request_budget_exceeded" in codes(result) and not client.calls
    result = get_data(request("financial_statements_standardized", symbols=["AAPL", "MSFT"]), registry=registry)
    assert "fmp_request_budget_exceeded" in codes(result) and not client.calls
    context = RequestContext()
    context.cancel()
    assert "cancelled" in codes(get_data(request(), registry=registry, context=context))
    assert not client.calls
    result = get_data(request(), registry=registry, context=RequestContext(account_scope="other"))
    assert not result.records and not client.calls


@pytest.mark.parametrize("changes", [{"isEtf": True}, {"isFund": True}, {"exchange": "LSE"},
    {"currency": "GBP"}, {"symbol": "OTHER"}, {"isFund": None}, {"companyName": ""}])
def test_wrong_listing_or_malformed_profile_is_not_authoritative(changes):
    registry, client, _ = setup()
    client.rows["profile"][0].update(changes)
    result = get_data(request("securities"), registry=registry)
    assert not result.records and len(client.calls) == 1
    assert codes(result).intersection({"upstream_schema", "unsupported"})


@pytest.mark.parametrize("changes", [{"price": None}, {"price": -1}, {"price": True}, {"price": "NaN"},
    {"volume": -1}, {"symbol": "OTHER"}, {"date": "2026-02-30"}, {"date": "2099-01-01"}])
def test_bad_daily_rows_fail_closed(changes):
    registry, client, _ = setup()
    client.rows["historical-price-eod/light"][0].update(changes)
    result = get_data(request(), registry=registry)
    assert not result.records and "upstream_schema" in codes(result)


@pytest.mark.parametrize("changes", [{"period": "Q1"}, {"date": "2099-01-01"}, {"reportedCurrency": ""},
    {"fiscalYear": "FY25"}, {"filingDate": "2020-01-01"}, {"acceptedDate": "2025-13-01 01:00:00"},
    {"revenue": "Infinity"}, {"netIncome": True}])
def test_bad_financial_rows_fail_closed(changes):
    registry, client, _ = setup()
    client.rows["income-statement"][0].update(changes)
    result = get_data(request("financial_statements_standardized"), registry=registry)
    assert not result.records and "upstream_schema" in codes(result)


def test_duplicates_empty_and_no_fallback_on_entitlement():
    from ir_search.registry import DataAdapterError
    registry, client, _ = setup()
    client.rows["historical-price-eod/light"] *= 2
    assert "upstream_schema" in codes(get_data(request(), registry=registry))
    client.rows["profile"] = []
    assert "not_found" in codes(get_data(request(), registry=registry))
    client.error = DataAdapterError("entitlement_denied")
    result = get_data(request(), registry=registry)
    assert result.status.value == "error" and not result.records
    assert "fallback_used" not in codes(result)
    client.error = RuntimeError("sensitive-upstream-url?apikey=" + KEY)
    payload = mcp_server.get_data_payload(request().to_dict(), registry=registry)
    assert KEY not in json.dumps(payload) and payload["status"] == "error"


def test_mcp_payload_and_real_registration(monkeypatch):
    registry, _, _ = setup()
    payload = mcp_server.get_data_payload(request().to_dict(), registry=registry)
    assert payload["status"] == "partial" and payload["records"][0]["price"] == "237.1200"
    runtime = pytest.importorskip("mcp.server.fastmcp")
    server = runtime.FastMCP("fmp-offline-validation")
    monkeypatch.setattr(server, "run", lambda: None)
    monkeypatch.setattr(mcp_server, "make_fastmcp", lambda _: server)
    original = mcp_server.get_data_payload
    monkeypatch.setattr(mcp_server, "get_data_payload", lambda data, **kwargs: original(data, registry=registry, **kwargs))
    mcp_server.run()

    async def check():
        response = await server.call_tool("get_data", {"dataset": "financial_statements_standardized", "market": "US",
            "symbols": ["AAPL"], "start": "2024-01-01", "end": "2026-09-14", "fields": ["revenue"]})
        result = json.loads(response[0].text)
        assert result["status"] == "partial" and result["records"][0]["revenue"] == "100"
        assert len(await server.list_tools()) == 12
    asyncio.run(check())
