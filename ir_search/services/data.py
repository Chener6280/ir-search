from __future__ import annotations

import copy
import math
from decimal import Decimal
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

from ir_search.context import RequestContext, RequestStopped
from ir_search.contracts import (
    AccessStatus, AdapterMode, DataCapability, DataPage, DataRequest,
    DataResult, Diagnostic, Status, ValueKind, _day, _instant,
)
from ir_search.models import EvidenceType, FailureKind
from ir_search.registry import DataAdapterError, DataRegistry, _DATASETS, build_data_registry
from ir_search.source_policy import _route_for, RECENT_INTRADAY_DAYS


def get_data(request: DataRequest, *, registry: Optional[DataRegistry] = None,
             context: Optional[RequestContext] = None) -> DataResult:
    """Read a validated page; default registries honor the explicit Wind/JYDB source policy."""
    if not isinstance(request, DataRequest):
        raise ValueError("get_data requires a DataRequest")
    context = context if context is not None else RequestContext()
    default_registry = registry is None
    registry = registry if registry is not None else build_data_registry()
    result = DataResult(request, context.request_id, definition=_DATASETS.get(request.dataset))
    result.diagnostics.extend(registry.diagnostics)
    route = _route_for(request) if registry.use_source_policy else None
    if route and route.horizon == "recent_only":
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        if request.start and request.start < today - timedelta(days=RECENT_INTRADAY_DAYS - 1):
            result.diagnostics.append(_diag("historical_intraday_source_not_configured", FailureKind.UNIMPLEMENTED))
            return result
        if request.end and request.end > today:
            result.diagnostics.append(_diag("future_intraday_not_supported", FailureKind.BLOCKED_BY_POLICY))
            return result
    if result.definition is None:
        if route:
            result.diagnostics.append(_diag("dataset_mapping_not_implemented", FailureKind.UNIMPLEMENTED))
        result.diagnostics.append(_diag("unknown_dataset", FailureKind.UNIMPLEMENTED))
        return result
    known_fields = {item.name for item in result.definition.fields}
    if not set(request.fields) <= known_fields:
        result.status = Status.ERROR
        result.diagnostics.append(_diag("unknown_fields", FailureKind.BLOCKED_BY_POLICY))
        return result
    if request.dataset != "securities" and (not request.symbols or not request.start):
        result.status = Status.ERROR
        result.diagnostics.append(_diag("symbols_and_date_range_required", FailureKind.BLOCKED_BY_POLICY))
        return result
    if request.dataset == "securities" and request.start:
        result.diagnostics.append(_diag("date_range_not_supported", FailureKind.UNIMPLEMENTED))
        return result
    if request.value_kind != ValueKind.ACTUAL:
        result.diagnostics.append(_diag("dataset_requires_actual_values", FailureKind.BLOCKED_BY_POLICY))
        return result
    if request.cursor and not request.provider:
        result.diagnostics.append(_diag("pagination_requires_explicit_provider", FailureKind.BLOCKED_BY_POLICY))
        return result
    entries = [(cap, adapter) for cap, adapter in registry.entries(account_scope=context.account_scope)
               if cap.dataset == request.dataset and (not request.provider or cap.provider == request.provider)]
    if route and not request.provider:
        entries = [(cap, adapter) for provider in route.providers for cap, adapter in entries if cap.provider == provider]
    if not entries:
        if not result.diagnostics:
            result.diagnostics.append(_diag("no_data_provider_registered", FailureKind.UNIMPLEMENTED))
        if default_registry and not registry.entries():
            # Nothing is enabled on this computer: this is configuration, not a missing feature.
            from ir_search.infrastructure.credentials import setup_hint
            result.diagnostics.append(Diagnostic(code="no_data_source_enabled", operation="query_data",
                                                 failure_kind=FailureKind.NO_CREDENTIAL, message=setup_hint()))
        return result
    can_fallback = bool(route and not request.provider and not request.cursor)
    if can_fallback and any(d.operation == "configure" and d.provider == route.providers[0] for d in registry.diagnostics):
        # Missing coverage and a broken enabled source are different situations.
        result.status = Status.ERROR
        result.diagnostics.append(_diag("primary_source_configuration_error", FailureKind.BLOCKED_BY_POLICY))
        return result
    for index, (cap, adapter) in enumerate(entries):
        reason = _incompatibility(request, cap, result.definition)
        if reason:
            result.diagnostics.append(_diag(reason, FailureKind.BLOCKED_BY_POLICY, cap))
            if can_fallback and reason in {"non_live_provider_blocked", "generated_data_blocked", "entitlement_denied"}:
                return result
            continue
        fallback = can_fallback and cap.provider != route.providers[0]
        if fallback and not any(d.provider == route.providers[0] for d in result.diagnostics):
            result.diagnostics.append(Diagnostic("primary_provider_not_registered", "query_data", provider=route.providers[0]))
        if can_fallback:
            result.diagnostics.append(_diag("provider_attempted", FailureKind.NONE, cap))
        try:
            context.begin_operation()
            page = adapter.query_data(request, context=context)
            context.check_active()
            _validate_page(page, request, cap, result.definition)
            result.diagnostics.extend(Diagnostic(
                d.code, "query_data", provider=cap.provider, failure_kind=d.failure_kind,
                adapter_mode=cap.adapter_mode, message="provider_reported",
            ) for d in page.diagnostics)
            if not page.records and page.complete:
                result.diagnostics.append(_diag("no_rows", FailureKind.NONE, cap))
                if can_fallback and index + 1 < len(entries) and not any(d.failure_kind != FailureKind.NONE for d in page.diagnostics):
                    continue
            result.records = copy.deepcopy(page.records)
            result.provenance = page.provenance
            result.complete = page.complete and not any(d.failure_kind != FailureKind.NONE for d in page.diagnostics)
            result.next_cursor = page.next_cursor
            result.status = Status.OK if result.complete else Status.PARTIAL
            if fallback:
                result.diagnostics.append(_diag("fallback_used", FailureKind.NONE, cap))
            if cap.access == AccessStatus.UNKNOWN:
                result.diagnostics.append(_diag("access_not_preverified", FailureKind.NONE, cap))
            if not result.complete:
                result.diagnostics.append(_diag("incomplete_page", FailureKind.NONE, cap))
                if not request.allow_partial:
                    result.records = []
                    result.next_cursor = None
                    result.status = Status.UNAVAILABLE
                    result.diagnostics.append(_diag("partial_data_not_allowed", FailureKind.BLOCKED_BY_POLICY, cap))
            return result
        except RequestStopped as exc:
            result.diagnostics.append(_diag(exc.code, exc.failure_kind, cap))
            return result
        except DataAdapterError as exc:
            result.diagnostics.append(_diag(exc.code, exc.failure_kind, cap))
            if can_fallback and exc.code in {"unsupported", "not_found"} and index + 1 < len(entries):
                continue
            result.status = Status.UNAVAILABLE if exc.code == "not_found" else Status.ERROR
            return result
        except (ValueError, TypeError, AttributeError, KeyError):
            result.status = Status.ERROR
            result.diagnostics.append(_diag("invalid_provider_response", FailureKind.UPSTREAM_SCHEMA, cap))
            return result
        except Exception:
            result.status = Status.ERROR
            # Provider exceptions may contain URLs, SQL, tokens or connection strings.
            result.diagnostics.append(_diag("provider_call_failed", FailureKind.UNKNOWN, cap))
            return result
    return result


def _diag(code, failure_kind, cap=None):
    return Diagnostic(code=code, operation="query_data", failure_kind=failure_kind,
                      provider=cap.provider if cap else None,
                      adapter_mode=cap.adapter_mode if cap else AdapterMode.UNKNOWN)


def _required_fields(request, definition):
    selected = set(request.fields) if request.fields else {f.name for f in definition.fields if f.name != "available_at"}
    return selected | set(definition.primary_key) | ({definition.date_field} if definition.date_field else set()) | ({"currency"} if any(f.name == "currency" for f in definition.fields) else set())


def _incompatibility(request, cap, definition):
    if cap.adapter_mode != AdapterMode.LIVE:
        return "non_live_provider_blocked"
    if cap.generated:
        return "generated_data_blocked"
    if cap.access == AccessStatus.DENIED:
        return "entitlement_denied"
    if not _required_fields(request, definition) <= set(cap.fields):
        return "fields_not_supported"
    if request.market not in cap.markets:
        return "market_not_supported"
    if request.frequency not in cap.frequencies:
        return "frequency_not_supported"
    if request.adjustment not in cap.adjustments:
        return "adjustment_not_supported"
    if request.value_kind not in cap.value_kinds:
        return "value_kind_not_supported"
    if request.start and cap.history_start and request.start < cap.history_start:
        return "history_not_covered"
    if request.end and cap.history_end and request.end > cap.history_end:
        return "history_not_covered"
    if request.as_of and not cap.supports_as_of:
        return "as_of_not_supported"
    if request.cursor and not cap.supports_pagination:
        return "pagination_not_supported"
    return None


def _validate_page(page, request, cap, definition):
    if not isinstance(page, DataPage) or type(page.complete) is not bool:
        raise ValueError("Expected a typed data page")
    if not isinstance(page.records, list) or len(page.records) > request.limit:
        raise ValueError("Invalid page size")
    if page.next_cursor is not None:
        if not isinstance(page.next_cursor, str) or not page.next_cursor or page.complete or not cap.supports_pagination:
            raise ValueError("Invalid pagination state")
    if any(not isinstance(item, Diagnostic) for item in page.diagnostics):
        raise ValueError("Untyped diagnostics")
    source = page.provenance
    if source.provider != cap.provider or source.adapter_mode != cap.adapter_mode or source.generated:
        raise ValueError("Source provenance mismatch")
    if source.evidence_type != EvidenceType.DATA_TABLE:
        raise ValueError("A data response must be a data table")
    schema = {f.name: f for f in definition.fields}
    required = _required_fields(request, definition)
    keys = set()
    for row in page.records:
        if not isinstance(row, dict) or not required <= set(row) or not set(row) <= set(schema) & set(cap.fields):
            raise ValueError("Row fields do not match dataset")
        for name, value in row.items():
            spec = schema[name]
            if value is None:
                if not spec.nullable:
                    raise ValueError("Required value is missing")
                continue
            if spec.dtype == "number":
                if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
                    raise ValueError("Invalid number")
                if isinstance(value, Decimal) and not value.is_finite():
                    raise ValueError("Non-finite number")
                if isinstance(value, float) and not math.isfinite(value):
                    raise ValueError("Non-finite number")
            elif spec.dtype == "string" and (not isinstance(value, str) or not value.strip()):
                raise ValueError("Invalid string")
            elif spec.dtype == "date":
                if _day(value) is None:
                    raise ValueError("Missing date")
            elif spec.dtype == "datetime":
                _instant(value)
        key = tuple(_day(row[f]) if schema[f].dtype == "date" else
                    _instant(row[f]) if schema[f].dtype == "datetime" else row[f] for f in definition.primary_key)
        if key in keys:
            raise ValueError("Duplicate dataset primary key")
        keys.add(key)
        if request.symbols and row["symbol"] not in request.symbols:
            raise ValueError("Unrequested security")
        if "market" in row and row["market"] != request.market:
            raise ValueError("Market mismatch")
        if definition.date_field and request.start:
            if not request.start <= _day(row[definition.date_field]) <= request.end:
                raise ValueError("Record outside requested date range")
        if request.dataset in {"prices_intraday", "options_intraday"}:
            if _instant(row["bar_time"]).astimezone(ZoneInfo("Asia/Shanghai")).date() != _day(row["trade_date"]):
                raise ValueError("Bar timestamp and trading date disagree")
        if request.dataset == "futures_intraday":
            if _instant(row["bar_time"]).astimezone(ZoneInfo("Asia/Shanghai")).date() != _day(row["calendar_date"]):
                raise ValueError("Bar timestamp and calendar date disagree")
        if request.dataset in {"prices_daily", "prices_intraday", "futures_intraday", "futures_daily", "options_daily", "options_intraday", "fund_exchange_daily", "derivatives_bars"}:
            if request.dataset in {"prices_daily", "prices_intraday"} and any(
                    row.get(name) is not None and row[name] < 0 for name in ("open", "high", "low", "close")):
                raise ValueError("Negative A-share price")
            for name in ("volume", "amount", "open_interest", "source_volume", "source_open_interest", "source_turnover"):
                if row.get(name) is not None and row[name] < 0:
                    raise ValueError("Negative activity count")
            if request.dataset in {"options_daily", "options_intraday"} and any(
                row.get(name) is not None and row[name] < 0 for name in ('open', 'high', 'low', 'close', 'settlement', 'price', 'average_price')):
                raise ValueError('Negative option price')
            high, low = row.get("high"), row.get("low")
            if high is not None and low is not None and high < low:
                raise ValueError("Inverted OHLC range")
            for name in ("open", "close"):
                value = row.get(name)
                if name == 'close' and row.get('price_status') == 'no_trade_reference':
                    continue
                if value is not None and ((high is not None and value > high) or (low is not None and value < low)):
                    raise ValueError("OHLC value outside range")
        if request.as_of:
            available = _instant(row.get("available_at"))
            if available is None or available > request.as_of:
                raise ValueError("Record was not demonstrably available at as_of")
    page.to_dict()  # Validate nested payload serialization before it crosses the boundary.
