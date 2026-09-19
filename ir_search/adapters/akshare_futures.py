"""Recent native-contract Sina futures bars; no fabricated night-session calendar."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from .akshare_intraday import _fetch, _CN, _PERIODS
from ir_search.contracts import AdapterMode, DataCapability, DataPage, Diagnostic, Provenance
from ir_search.models import SourceAuthority
from ir_search.registry import DataAdapterError
from ir_search.source_policy import RECENT_INTRADAY_DAYS
from ir_search.infrastructure.calendars import _future_calendars, _resolve_bar_date

_CONTRACT = re.compile(r"[A-Z]{1,3}\d{1,2}(?:0[1-9]|1[0-2])")
_COLUMNS = {"open": "open", "high": "high", "low": "low", "close": "close",
            "source_volume": "volume", "source_open_interest": "hold"}
_FIELDS = ("symbol", "bar_time", "calendar_date", "trade_date", "currency", "trade_date_source") + tuple(_COLUMNS)


class AKShareFuturesAdapter:
    name = "akshare"

    def __init__(self, *, fetch=None, now=None, calendar_profile=None, calendars=None):
        self._fetch = fetch or _fetch
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._calendar_profile = calendar_profile
        self._calendars = calendars or _future_calendars
        self.capabilities = (DataCapability(
            self.name, "futures_intraday", _FIELDS, ("CN_FUTURES",), frequencies=_PERIODS,
            adapter_mode=AdapterMode.LIVE,
            coverage_notes=("Sina native concrete contract codes, e.g. IF2609 or RB2610; continuous contracts excluded",
                            "Requested start/end are calendar dates in Asia/Shanghai, not exchange trading dates",
                            "Published Wind open dates resolve observed night bars when configured; otherwise trade_date is null",
                            "Native quote units and activity counting conventions; no notional or P&L conversion",
                            "Recent source window only; per-contract coverage must be verified live"),
        ),)

    def query_data(self, request, *, context):
        """Return bounded recent bars with explicit unresolved calendar/unit semantics."""
        if (request.dataset != "futures_intraday" or request.market != "CN_FUTURES"
                or request.frequency not in _PERIODS or request.adjustment != "raw"
                or request.value_kind.value != "actual" or request.as_of or request.cursor
                or not request.start or not 1 <= len(request.symbols) <= 5
                or context.account_scope != "default"
                or any(not _CONTRACT.fullmatch(symbol) for symbol in request.symbols)):
            raise DataAdapterError("unsupported")
        now = self._now().astimezone(_CN)
        if request.start < now.date() - timedelta(days=RECENT_INTRADAY_DAYS - 1):
            raise DataAdapterError("historical_intraday_source_not_configured")
        if request.end > now.date():
            raise DataAdapterError("unsupported")
        fields = (set(request.fields) if request.fields else set(_FIELDS)) | set(_FIELDS[:6])
        if not fields <= set(_FIELDS):
            raise DataAdapterError("unsupported")
        records, seen = [], set()
        issues = {"contract_quote_unit_not_normalized",
                  "activity_counting_convention_unverified", "recent_intraday_coverage_unverified",
                  "web_quote_delay_unverified", "live_bar_may_be_incomplete"}
        calendars={}
        if self._calendar_profile is not None:
            try:
                calendars,missing=self._calendars(self._calendar_profile,request.symbols,request.start,request.end,context)
                issues.add('night_session_schedule_not_independently_verified')
                if missing:issues.add('calendar_contract_mapping_incomplete')
                if self._calendar_profile.tls_mode=='disabled':issues.add('calendar_non_tls_explicitly_configured')
            except DataAdapterError as exc:
                issues.add('wind_calendar_'+exc.code)
        if not calendars:issues.add('trade_date_not_resolved')
        for symbol in request.symbols:
            rows = self._fetch("futures_zh_minute_sina", {"symbol": symbol, "period": request.frequency[:-1]}, context=context)
            context.check_active()
            if not isinstance(rows, list) or len(rows) > 10000:
                raise DataAdapterError("upstream_schema")
            included = 0
            for raw in rows:
                if not isinstance(raw, dict) or not {"datetime", *_COLUMNS.values()} <= set(raw):
                    raise DataAdapterError("upstream_schema")
                try:
                    stamp = datetime.fromisoformat(raw["datetime"])
                    stamp = stamp.replace(tzinfo=_CN) if stamp.tzinfo is None else stamp.astimezone(_CN)
                except (TypeError, ValueError):
                    raise DataAdapterError("upstream_schema") from None
                if not request.start <= stamp.date() <= request.end:
                    continue
                if stamp > now:
                    if stamp <= now + timedelta(minutes=int(request.frequency[:-1])):
                        issues.add('unclosed_bar_excluded')
                        continue
                    raise DataAdapterError('upstream_schema')
                if (symbol, stamp) in seen:
                    raise DataAdapterError("upstream_schema")
                seen.add((symbol, stamp))
                trading_day=_resolve_bar_date(stamp,calendars.get(symbol,set()))
                if trading_day is None:issues.add('trade_date_not_resolved')
                else:
                    issues.add('trade_date_resolved_from_published_calendar')
                    if stamp.hour >= 21 or stamp.hour < 6:
                        issues.add('night_date_resolved_from_published_calendar')
                row = {"symbol": symbol, "bar_time": stamp, "calendar_date": stamp.date(), "trade_date": trading_day,
                       "trade_date_source":'wind_mysql_published_open_dates' if trading_day else None, "currency": "CNY"}
                for name, column in _COLUMNS.items():
                    if name not in fields:
                        continue
                    value = raw[column]
                    try:
                        if isinstance(value, bool):
                            raise ValueError()
                        value = Decimal(str(value)) if value is not None else None
                    except (ValueError, InvalidOperation):
                        raise DataAdapterError("upstream_schema") from None
                    if value is not None and not value.is_finite():
                        value = None
                    if value is None:
                        issues.add("missing_source_values")
                    row[name] = value
                records.append(row)
                included += 1
            if not included:
                issues.add("no_intraday_rows_for_requested_symbol")
            elif max(row['calendar_date'] for row in records if row['symbol'] == symbol) < request.end:
                issues.add('requested_end_date_without_bars')
        records.sort(key=lambda row: (row["symbol"], row["bar_time"]))
        if len(records) > request.limit:
            issues.add("row_limit_reached_narrow_request")
        return DataPage(records[:request.limit], Provenance(self.name, "Sina via AKShare", self._now(),
                        authority=SourceAuthority.DATA_VENDOR, adapter_mode=AdapterMode.LIVE), complete=False,
                        diagnostics=[Diagnostic(code, "query_data", provider=self.name, adapter_mode=AdapterMode.LIVE)
                                     for code in sorted(issues)])
