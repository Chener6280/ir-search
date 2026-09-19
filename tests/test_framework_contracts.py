from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
import json

import pytest

from ir_search import (
    AdapterMode, AdvisoryResult, DataCapability, DataPage, DataRequest, Diagnostic,
    MaterialRequest, Provenance, RequestContext,
)
from ir_search.context import RequestStopped
from ir_search.models import FailureKind, SourceTier


NOW = datetime(2026, 9, 13, tzinfo=timezone.utc)


def test_request_normalizes_dates_enums_and_immutable_sequences():
    request = DataRequest("prices_daily", symbols=["000001.SZ"], start="2026-09-01", end="2026-09-10",
                          as_of="2026-09-13T00:00:00Z", value_kind="actual")
    assert request.symbols == ("000001.SZ",)
    assert request.start == date(2026, 9, 1)
    assert request.frequency == "1d" and request.adjustment == "raw"
    assert request.to_dict()["as_of"] == NOW.isoformat()
    directory = DataRequest("securities")
    assert directory.frequency == "snapshot" and directory.adjustment == "none"


@pytest.mark.parametrize("kwargs", [
    {"symbols": "000001.SZ"}, {"symbols": ["A", "A"]}, {"fields": [""]},
    {"start": "2026-09-10"}, {"start": "2026-09-10", "end": "2026-09-01"},
    {"start": NOW, "end": NOW}, {"start": 20260901, "end": 20260910},
    {"start": "20260901", "end": "20260910"}, {"start": "2026-W36-1", "end": "2026-09-10"},
    {"as_of": "2026-09-01"}, {"as_of": NOW.replace(tzinfo=None)},
    {"limit": True}, {"limit": 0}, {"limit": 5001}, {"allow_partial": "false"},
    {"value_kind": "made_up"}, {"cursor": ""}, {"provider": ""}, {"market": ""},
])
def test_invalid_data_requests_are_rejected(kwargs):
    with pytest.raises((ValueError, TypeError)):
        DataRequest("prices_daily", **kwargs)


def test_json_preserves_decimal_precision_and_source_enums():
    provenance = Provenance("test_vendor", "fixture", NOW, source_tier=SourceTier.EXCHANGE_FILING,
                            adapter_mode="live")
    page = DataPage([{"value": Decimal("1234567890.123456789"), "date": date(2026, 9, 1)}], provenance)
    payload = page.to_dict()
    assert payload["records"][0]["value"] == "1234567890.123456789"
    assert payload["provenance"]["source_tier"] == "EXCHANGE_FILING"
    json.dumps(payload, allow_nan=False)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), Decimal("NaN"), object(), {1: "bad key"}])
def test_json_rejects_unsupported_and_nonfinite_values(value):
    page = DataPage([{"value": value}], Provenance("test", "fixture", NOW))
    with pytest.raises((TypeError, ValueError)):
        page.to_dict()


def test_provenance_and_advisory_require_explicit_timestamps():
    with pytest.raises(ValueError):
        Provenance("test", "fixture", None)
    with pytest.raises(ValueError):
        AdvisoryResult("service", "opinion", None)
    advisory = AdvisoryResult("service", "opinion", NOW)
    assert advisory.to_dict()["generated"] is True
    with pytest.raises(TypeError):
        AdvisoryResult("service", "opinion", NOW, generated=False)
    assert Diagnostic("timeout", "query_data", failure_kind="timeout").failure_kind == FailureKind.TIMEOUT
    with pytest.raises(ValueError):
        Diagnostic("raw exception with secrets", "query_data")


def test_capability_normalizes_and_validates_coverage():
    cap = DataCapability("test", "prices_daily", ["symbol"], ["A_SHARE"],
                         history_start="2026-01-01", adapter_mode="live", verified_at=NOW)
    assert cap.fields == ("symbol",) and cap.adapter_mode == AdapterMode.LIVE
    with pytest.raises(ValueError):
        replace(cap, history_end="2025-12-31")
    with pytest.raises(ValueError):
        replace(cap, supports_as_of="yes")


@pytest.mark.parametrize("kwargs", [
    {"urls": []}, {"urls": "https://example.com"}, {"urls": [f"https://example.com/{i}" for i in range(11)]},
    {"max_chars": 0}, {"max_chars": 100001}, {"max_spans": False}, {"max_spans": 51},
])
def test_material_request_limits(kwargs):
    args = {"urls": ["https://example.com"], **kwargs}
    with pytest.raises(ValueError):
        MaterialRequest("question", **args)


def test_context_budget_cancellation_and_remaining_timeout():
    clock = [10.0]
    context = RequestContext(timeout_seconds=5, max_operations=1, _clock=lambda: clock[0])
    context.begin_operation()
    with pytest.raises(RequestStopped, match="operation_budget_exhausted"):
        context.begin_operation()
    clock[0] += 2
    assert context.remaining_seconds() == 3
    context.check_active()
    clock[0] += 4
    assert context.remaining_seconds() == 0
    with pytest.raises(RequestStopped, match="deadline_exceeded"):
        context.check_active()
    context.cancel()
    with pytest.raises(RequestStopped, match="cancelled"):
        context.check_active()


@pytest.mark.parametrize("kwargs", [
    {"timeout_seconds": 0}, {"timeout_seconds": float("nan")}, {"timeout_seconds": True},
    {"max_operations": True}, {"max_operations": 0}, {"account_scope": ""},
])
def test_context_rejects_unbounded_or_invalid_budgets(kwargs):
    with pytest.raises(ValueError):
        RequestContext(**kwargs)
