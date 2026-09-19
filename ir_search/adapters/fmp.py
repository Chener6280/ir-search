"""FMP stable US profiles, basic daily prices and annual standardized statements."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from ir_search.contracts import AdapterMode, DataCapability, DataPage, Diagnostic, Provenance
from ir_search.contracts.overseas import FINANCIAL_METADATA, FINANCIAL_METRICS, OVERSEAS_DATASETS
from ir_search.infrastructure.credentials import FMPProfile
from ir_search.infrastructure.fmp import _Budget, _client_for, _TICKER
from ir_search.models import SourceAuthority
from ir_search.registry import DataAdapterError

_SECURITY_FIELDS = ("symbol", "name", "market", "exchange", "currency", "available_at")
_ENDPOINTS = {"income": "income-statement", "balance": "balance-sheet-statement", "cashflow": "cash-flow-statement"}
_METRICS = {name for mapping in FINANCIAL_METRICS.values() for name in mapping}
_META = {item.name for item in FINANCIAL_METADATA}


def _day(value):
    try:
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError()
        return date.fromisoformat(value)
    except ValueError:
        raise DataAdapterError("upstream_schema") from None


def _number(value):
    if value is None:
        return None
    try:
        if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
            raise ValueError()
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError()
        return result
    except (ValueError, InvalidOperation):
        raise DataAdapterError("upstream_schema") from None


def _text(value, *, pattern=None):
    if (not isinstance(value, str) or not value.strip() or len(value) > 512
            or any(ord(char) < 32 for char in value) or (pattern and not re.fullmatch(pattern, value))):
        raise DataAdapterError("upstream_schema")
    return value


class FMPAdapter:
    name = "fmp"

    def __init__(self, profile: FMPProfile, *, client=None, now=None):
        if not isinstance(profile, FMPProfile):
            raise ValueError("FMPProfile required")
        self._profile = profile
        self._client = client if client is not None else _client_for(profile)
        self._now = now or (lambda: datetime.now(timezone.utc))
        common = ("Specific US-listed company tickers only; current NASDAQ/NYSE/AMEX USD profile required",
                  "Account endpoint/symbol/history entitlement is checked by each response, not inferred from plan name",
                  "Fixed stable endpoints; no retries; one request start per second per local client",
                  "Per-query request budget; bounded memory cache; no cross-process daily quota accounting",
                  "Vendor data, not a direct filing; no point-in-time or full revision history")
        self.capabilities = tuple(DataCapability(
            self.name, dataset, fields, ("US",), frequencies=(frequency,), adjustments=(adjustment,),
            adapter_mode=AdapterMode.LIVE, coverage_notes=common + notes,
        ) for dataset, fields, frequency, adjustment, notes in (
            ("securities", _SECURITY_FIELDS, "snapshot", "none", ("No full-universe enumeration or ETFs/funds",)),
            ("prices_daily_basic", tuple(f.name for f in OVERSEAS_DATASETS["prices_daily_basic"].fields), "1d", "source_unspecified",
             ("Maximum 366 calendar days per query; completeness, price adjustment and volume convention unverified",)),
            ("financial_statements_standardized", tuple(f.name for f in OVERSEAS_DATASETS["financial_statements_standardized"].fields), "annual", "none",
             ("Latest bounded annual FY records per selected statement, filtered by fiscal period end",
              "Original/restated, consolidation scope and accepted timestamp timezone are unverified")),
        ))

    def query_data(self, request, *, context):
        """Return normalized vendor rows with explicit source and coverage diagnostics."""
        context.check_active()
        cap = next((c for c in self.capabilities if c.dataset == request.dataset), None)
        if (cap is None or request.market != "US" or request.frequency not in cap.frequencies
                or request.adjustment not in cap.adjustments or request.value_kind.value != "actual"
                or request.as_of or request.cursor or context.account_scope != "default"
                or not 1 <= len(request.symbols) <= 5 or any(not _TICKER.fullmatch(s) for s in request.symbols)):
            raise DataAdapterError("unsupported")
        fields = set(request.fields or cap.fields)
        if not fields <= set(cap.fields):
            raise DataAdapterError("unsupported")
        snapshot = request.dataset == "securities"
        financial = request.dataset == "financial_statements_standardized"
        today = self._now().astimezone(timezone.utc).date()
        if ((snapshot and request.start is not None)
                or (not snapshot and (request.start is None or request.end is None or request.end > today))
                or (not snapshot and not financial and (request.end - request.start).days > 365)):
            raise DataAdapterError("unsupported")
        statements = [name for name, mapping in FINANCIAL_METRICS.items() if fields.intersection(mapping)]
        if financial and not statements:
            statements = list(FINANCIAL_METRICS)
        needed = len(request.symbols) * (1 + (len(statements) if financial else 0 if snapshot else 1))
        if needed > self._profile.max_requests_per_query:
            raise DataAdapterError("fmp_request_budget_exceeded")
        # Preflight worst-case requests even if some calls may be served from cache.
        budget = _Budget(self._profile.max_requests_per_query)
        responses, records, issues = [], [], {"vendor_data_not_direct_filing"}

        def fetch(endpoint, params):
            response = self._client.fetch(endpoint, params, context=context, budget=budget)
            context.check_active()
            responses.append(response)
            if response.cache_hit:
                issues.add("memory_cache_hit")
            return response.records

        for symbol in request.symbols:
            profile_rows = fetch("profile", {"symbol": symbol})
            if not profile_rows:
                raise DataAdapterError("not_found")
            if len(profile_rows) != 1:
                raise DataAdapterError("upstream_schema")
            profile = profile_rows[0]
            if profile.get("symbol") != symbol:
                raise DataAdapterError("upstream_schema")
            exchange = _text(profile.get("exchange"))
            currency = _text(profile.get("currency"), pattern=r"[A-Z]{3}")
            if exchange not in {"NASDAQ", "NYSE", "AMEX"} or currency != "USD":
                raise DataAdapterError("unsupported")
            if any(type(profile.get(flag)) is not bool for flag in ("isEtf", "isFund")):
                raise DataAdapterError("upstream_schema")
            if profile["isEtf"] or profile["isFund"]:
                raise DataAdapterError("unsupported")
            if snapshot:
                row = {"symbol": symbol, "name": _text(profile.get("companyName")), "market": "US",
                       "exchange": exchange, "currency": currency, "available_at": None}
                records.append({k: v for k, v in row.items() if k in fields | {"symbol", "currency"}})
            elif not financial:
                issues.update({"daily_price_adjustment_unverified", "volume_convention_unverified",
                               "daily_window_completeness_unverified", "historical_currency_from_current_profile"})
                rows = fetch("historical-price-eod/light", {"symbol": symbol, "from": request.start.isoformat(), "to": request.end.isoformat()})
                seen = set()
                if len(rows) > 366:
                    raise DataAdapterError("upstream_schema")
                for raw in rows:
                    when = _day(raw.get("date"))
                    if raw.get("symbol") != symbol or when > today or when in seen:
                        raise DataAdapterError("upstream_schema")
                    seen.add(when)
                    price, volume = _number(raw.get("price")), _number(raw.get("volume"))
                    if price is None or price <= 0 or (volume is not None and volume < 0):
                        raise DataAdapterError("upstream_schema")
                    if volume is None:
                        issues.add("source_field_missing")
                    if not request.start <= when <= request.end:
                        issues.add("source_rows_outside_requested_window")
                        continue
                    row = {"symbol": symbol, "trade_date": when, "price": price, "source_volume": volume, "currency": currency}
                    records.append({k: v for k, v in row.items() if k in fields | {"symbol", "trade_date", "currency"}})
            else:
                issues.update({"annual_history_bounded", "current_snapshot_not_point_in_time",
                               "statement_scope_unverified", "accepted_timestamp_timezone_unverified",
                               "version_id_is_derived", "cross_statement_alignment_unverified"})
                for statement in statements:
                    rows = fetch(_ENDPOINTS[statement], {"symbol": symbol, "period": "annual", "limit": self._profile.annual_record_limit})
                    if len(rows) > self._profile.annual_record_limit:
                        raise DataAdapterError("upstream_schema")
                    seen = set()
                    for raw in rows:
                        row = self._financial_row(raw, symbol, statement, today)
                        when = row["report_period"]
                        if when in seen:
                            raise DataAdapterError("upstream_schema")
                        seen.add(when)
                        if request.start <= when <= request.end:
                            if any(row[name] is None for name in fields.intersection(FINANCIAL_METRICS[statement])):
                                issues.add("source_field_missing")
                            records.append({k: v for k, v in row.items() if k in fields | _META})
                    if seen and request.start < min(seen):
                        issues.add("annual_history_may_be_truncated")
                    if not any(request.start <= day <= request.end for day in seen):
                        issues.add("requested_statement_window_empty")
        records.sort(key=lambda row: (row["symbol"], row.get("trade_date", row.get("report_period", date.min)), row.get("statement", "")))
        complete = snapshot
        if len(records) > request.limit:
            issues.add("row_limit_reached")
            complete = False
        if not records:
            issues.add("no_rows_in_requested_window")
        diagnostics = [Diagnostic(code, "query_data", self.name, adapter_mode=AdapterMode.LIVE) for code in sorted(issues)]
        diagnostics.append(Diagnostic("fmp_request_usage", "query_data", self.name, adapter_mode=AdapterMode.LIVE,
                                      message=f"network_attempts={budget.used}; query_limit={budget.limit}; no automatic retries"))
        return DataPage(records[:request.limit], Provenance(self.name, "Financial Modeling Prep",
                        min(response.fetched_at for response in responses), authority=SourceAuthority.DATA_VENDOR,
                        adapter_mode=AdapterMode.LIVE), complete=complete, diagnostics=diagnostics)

    @staticmethod
    def _financial_row(raw, symbol, statement, today):
        when = _day(raw.get("date"))
        if raw.get("symbol") != symbol or raw.get("period") != "FY" or when > today:
            raise DataAdapterError("upstream_schema")
        fiscal_year = _text(raw.get("fiscalYear"), pattern=r"\d{4}")
        filing = _day(raw["filingDate"]) if raw.get("filingDate") else None
        accepted = raw.get("acceptedDate") or None
        if filing and (filing < when or filing > today):
            raise DataAdapterError("upstream_schema")
        if accepted is not None:
            _text(accepted, pattern=r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}")
            try:
                instant = datetime.fromisoformat(accepted)
                if instant.date() < when or instant.date() > today:
                    raise ValueError()
            except ValueError:
                raise DataAdapterError("upstream_schema") from None
        row = {"symbol": symbol, "report_period": when, "statement": statement, "fiscal_year": fiscal_year,
               "fiscal_period": "FY", "currency": _text(raw.get("reportedCurrency"), pattern=r"[A-Z]{3}"),
               "filing_date": filing, "accepted_at_source": accepted,
               "version_basis": "provider_current_snapshot", "statement_basis": "provider_standardized_scope_unverified"}
        mapping = FINANCIAL_METRICS[statement]
        row.update({name: _number(raw.get(mapping[name])) if name in mapping else None for name in sorted(_METRICS)})
        # Canonical decimal strings keep version IDs independent of requested fields and JSON numeric formatting.
        identity = {key: (str(value.normalize()) if value else "0") if isinstance(value, Decimal)
                    else value.isoformat() if isinstance(value, date) else value for key, value in row.items()}
        row["version_id"] = "sha256:" + hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return row
