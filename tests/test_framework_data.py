"""Test-only in-memory upstreams exercise the real registry and service boundary."""
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import json

import pytest

from ir_search import (
    AccessStatus, AdapterMode, DataAdapterError, DataCapability, DataPage, DataRegistry,
    DataRequest, Diagnostic, Provenance, RequestContext, Status, build_data_registry,
    describe_dataset, get_data, list_capabilities,
)
from ir_search.models import FailureKind


NOW = datetime(2026, 9, 13, tzinfo=timezone.utc)
PRICE_FIELDS = ("symbol", "trade_date", "open", "high", "low", "close", "volume", "amount", "currency", "available_at")


class Upstream:
    def __init__(self, name="test_vendor", **capability):
        self.name = name
        self.capabilities = (DataCapability(name, "prices_daily", PRICE_FIELDS, ("A_SHARE",),
                                            adapter_mode=AdapterMode.LIVE, access=AccessStatus.GRANTED, **capability),)
        self.calls = []
        self.error = None
        self.on_call = lambda: None
        self.page = DataPage([
            {"symbol": "000001.SZ", "trade_date": day, "open": 10, "high": 12, "low": 9,
             "close": Decimal("11.001"), "volume": 200, "amount": 2200, "currency": "CNY"}
            for day in ("2026-09-01", "2026-09-02")
        ], Provenance(name, "test fixture", NOW, adapter_mode=AdapterMode.LIVE), complete=True)

    def query_data(self, request, *, context):
        self.calls.append((request, context))
        self.on_call()
        if self.error:
            raise self.error
        return self.page


def run(upstream=None, request=None, context=None):
    upstream = upstream or Upstream()
    registry = DataRegistry()
    registry.register(upstream)
    request = request or DataRequest("prices_daily", symbols=["000001.SZ"], start="2026-09-01", end="2026-09-02")
    return get_data(request, registry=registry, context=context)


def test_no_provider_is_unavailable_not_a_mock_or_empty_success():
    request = DataRequest("prices_daily", symbols=["000001.SZ"], start="2026-09-01", end="2026-09-02")
    result = get_data(request)
    assert result.status == Status.UNAVAILABLE and not result.records and not result.complete
    assert result.diagnostics[0].code == "no_data_provider_registered"
    assert build_data_registry().entries() == ()
    assert list_capabilities()["capabilities"] == []
    assert describe_dataset("prices_daily")["definition"]["primary_key"] == ["symbol", "trade_date"]
    assert describe_dataset("prices_daily")["capabilities"] == []
    assert describe_dataset("not_defined")["diagnostics"][0]["code"] == "unknown_dataset"
    assert get_data(DataRequest("not_defined")).diagnostics[0].code == "unknown_dataset"
    with pytest.raises(ValueError):
        describe_dataset([])
    with pytest.raises(ValueError):
        get_data("prices_daily")


def test_two_dates_survive_and_precision_is_preserved():
    source = Upstream()
    result = run(source)
    assert result.status == Status.OK and result.complete
    assert [row["trade_date"] for row in result.records] == ["2026-09-01", "2026-09-02"]
    assert result.to_dict()["records"][0]["close"] == "11.001"
    assert source.calls[0][1].operations == 1
    source.page.records[0]["close"] = 0
    assert result.records[0]["close"] == Decimal("11.001")
    json.dumps(result.to_dict(), allow_nan=False)


@pytest.mark.parametrize("change, code", [
    ({"adapter_mode": AdapterMode.MOCK}, "non_live_provider_blocked"),
    ({"adapter_mode": AdapterMode.PLACEHOLDER}, "non_live_provider_blocked"),
    ({"adapter_mode": AdapterMode.FALLBACK}, "non_live_provider_blocked"),
    ({"generated": True}, "generated_data_blocked"),
    ({"access": AccessStatus.DENIED}, "entitlement_denied"),
    ({"markets": ("HK",)}, "market_not_supported"),
    ({"frequencies": ("snapshot",)}, "frequency_not_supported"),
    ({"adjustments": ("forward",)}, "adjustment_not_supported"),
    ({"history_start": "2026-09-02"}, "history_not_covered"),
    ({"history_end": "2026-09-01"}, "history_not_covered"),
    ({"fields": ("symbol", "trade_date", "close", "currency")}, "fields_not_supported"),
])
def test_incompatible_capabilities_do_not_call_upstream(change, code):
    source = Upstream()
    source.capabilities = (replace(source.capabilities[0], **change),)
    assert run(source).diagnostics[0].code == code
    assert not source.calls


def test_registration_is_atomic_and_account_scoped():
    source = Upstream()
    registry = DataRegistry()
    valid = source.capabilities[0]
    source.capabilities = (valid, replace(valid, dataset="undeclared"))
    with pytest.raises(ValueError):
        registry.register(source)
    assert not registry.entries()
    source.capabilities = (replace(valid, account_scope="team_a"),)
    registry.register(source)
    assert not registry.entries()
    assert len(registry.entries(account_scope="team_a")) == 1
    assert list_capabilities(registry=registry)["capabilities"] == []
    assert describe_dataset("prices_daily", registry=registry, account_scope="team_a")["capabilities"]
    with pytest.raises(ValueError):
        registry.register(source)
    assert run(source).status == Status.UNAVAILABLE
    assert run(source, context=RequestContext(account_scope="team_a")).status == Status.OK


@pytest.mark.parametrize("change", [{"provider": "wrong"}, {"fields": ("symbol",)}, {"fields": PRICE_FIELDS + ("password",)}, {"markets": ()}])
def test_registry_rejects_invalid_provider_declarations(change):
    source = Upstream()
    source.capabilities = (replace(source.capabilities[0], **change),)
    with pytest.raises(ValueError):
        DataRegistry().register(source)


@pytest.mark.parametrize("mutation", [
    lambda p: p.records.append(dict(p.records[0])),
    lambda p: p.records[0].update(symbol="OTHER"),
    lambda p: p.records[0].update(trade_date="2026-08-31"),
    lambda p: p.records[0].update(close=float("nan")),
    lambda p: p.records[0].update(close=True),
    lambda p: p.records[0].update(close="11.5"),
    lambda p: p.records[0].update(currency=None),
    lambda p: p.records[0].pop("close"),
    lambda p: p.records[0].update(password="must_not_escape"),
    lambda p: setattr(p, "provenance", replace(p.provenance, generated=True)),
    lambda p: setattr(p, "provenance", replace(p.provenance, provider="wrong")),
    lambda p: setattr(p, "next_cursor", "cursor_without_declared_pagination"),
])
def test_invalid_or_misleading_responses_are_rejected(mutation):
    source = Upstream()
    mutation(source.page)
    result = run(source)
    assert result.status == Status.ERROR and not result.records
    assert result.diagnostics[-1].code == "invalid_provider_response"
    assert "must_not_escape" not in json.dumps(result.to_dict())


def test_pagination_partial_failures_and_strict_requests():
    source = Upstream(supports_pagination=True)
    source.page.complete = False
    source.page.next_cursor = "page-2"
    source.page.diagnostics = [Diagnostic("rate_limit", "query_data", failure_kind=FailureKind.RATE_LIMIT,
                                          message="Authorization: Bearer must_not_escape")]
    result = run(source)
    assert result.status == Status.PARTIAL and len(result.records) == 2
    assert result.next_cursor == "page-2" and result.diagnostics[0].code == "rate_limit"
    assert "must_not_escape" not in json.dumps(result.to_dict())
    strict = run(source, replace(result.request, allow_partial=False))
    assert strict.status == Status.UNAVAILABLE and not strict.records and strict.next_cursor is None
    assert run(source, replace(result.request, cursor="page-2")).diagnostics[0].code == "pagination_requires_explicit_provider"
    resumed = run(source, replace(result.request, provider=source.name, cursor="page-2"))
    assert resumed.status == Status.PARTIAL
    assert source.calls[-1][0].cursor == "page-2"


def test_empty_success_is_distinct_from_incomplete_and_failed():
    source = Upstream()
    source.page.records = []
    result = run(source)
    assert result.status == Status.OK and result.complete and result.diagnostics[0].code == "no_rows"
    source.page.complete = False
    assert run(source).status == Status.PARTIAL


def test_as_of_requires_capability_and_per_record_availability():
    source = Upstream()
    request = DataRequest("prices_daily", symbols=["000001.SZ"], start="2026-09-01", end="2026-09-02", as_of=NOW)
    assert run(source, request).diagnostics[0].code == "as_of_not_supported"
    source.capabilities = (replace(source.capabilities[0], supports_as_of=True),)
    assert run(source, request).status == Status.ERROR
    for row in source.page.records:
        row["available_at"] = "2026-09-02T08:00:00Z"
    assert run(source, request).status == Status.OK
    source.page.records[0]["available_at"] = "2026-09-14T08:00:00Z"
    assert run(source, request).status == Status.ERROR


@pytest.mark.parametrize("code", list(DataAdapterError.KINDS))
def test_typed_adapter_errors_keep_their_reason(code):
    source = Upstream()
    source.error = DataAdapterError(code)
    result = run(source)
    assert result.status == (Status.UNAVAILABLE if code == "not_found" else Status.ERROR)
    assert result.diagnostics[-1].code == code


def test_unexpected_failure_does_not_leak_or_trigger_other_provider():
    first, second = Upstream("a"), Upstream("b")
    first.error = RuntimeError("Authorization: Bearer must_not_escape")
    registry = DataRegistry()
    registry.register(first)
    registry.register(second)
    request = DataRequest("prices_daily", symbols=["000001.SZ"], start="2026-09-01", end="2026-09-02")
    result = get_data(request, registry=registry)
    assert result.status == Status.ERROR and not second.calls
    assert "must_not_escape" not in json.dumps(result.to_dict())


def test_cancelled_and_late_requests_return_no_data():
    source = Upstream()
    context = RequestContext()
    context.cancel()
    assert run(source, context=context).diagnostics[0].code == "cancelled"
    assert not source.calls
    clock = [0]
    context = RequestContext(timeout_seconds=1, _clock=lambda: clock[0])
    source.on_call = lambda: clock.__setitem__(0, 2)
    result = run(source, context=context)
    assert not result.records and result.diagnostics[-1].code == "deadline_exceeded"


def test_requests_do_not_silently_ignore_fields_or_scope():
    assert get_data(DataRequest("prices_daily")).diagnostics[0].code == "symbols_and_date_range_required"
    assert get_data(DataRequest("securities", fields=["invented"])).diagnostics[0].code == "unknown_fields"
    assert get_data(DataRequest("securities", start="2026-09-01", end="2026-09-02")).diagnostics[0].code == "date_range_not_supported"
    assert get_data(DataRequest("securities", value_kind="estimate")).diagnostics[0].code == "dataset_requires_actual_values"


def test_security_directory_uses_snapshot_contract():
    source = Upstream()
    source.capabilities = (DataCapability(
        source.name, "securities", ("symbol", "name", "market", "exchange", "currency"),
        ("A_SHARE",), frequencies=("snapshot",), adjustments=("none",), adapter_mode="live",
    ),)
    source.page.records = [{"symbol": "000001.SZ", "name": "Test fixture", "market": "A_SHARE",
                            "exchange": "SZSE", "currency": "CNY"}]
    result = run(source, DataRequest("securities", symbols=["000001.SZ"]))
    assert result.status == Status.OK
    assert result.diagnostics[0].code == "access_not_preverified"
    assert result.definition.primary_key == ("symbol",)


def test_field_projection_still_requires_identifiers_and_currency():
    source = Upstream()
    source.capabilities = (replace(source.capabilities[0], fields=("symbol", "trade_date", "close", "currency")),)
    source.page.records = [{k: row[k] for k in ("symbol", "trade_date", "close", "currency")} for row in source.page.records]
    request = DataRequest("prices_daily", symbols=["000001.SZ"], start="2026-09-01", end="2026-09-02", fields=["close"])
    assert run(source, request).status == Status.OK
    assert run(source, replace(request, limit=1)).status == Status.ERROR


def test_failure_diagnostic_prevents_complete_claim():
    source = Upstream()
    source.page.diagnostics = [Diagnostic("network", "query_data", failure_kind="network")]
    result = run(source)
    assert result.status == Status.PARTIAL and result.complete is False
