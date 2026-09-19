"""Recent A-share bars with explicit Eastmoney or price-only Sina selection."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from ir_search.contracts import AdapterMode, DataCapability, DataPage, Diagnostic, Provenance
from ir_search.models import SourceAuthority
from ir_search.registry import DataAdapterError
from ir_search.source_policy import RECENT_INTRADAY_DAYS

_CN = ZoneInfo("Asia/Shanghai")
_PERIODS = ("1m", "5m", "15m", "30m", "60m")
_SYMBOL = re.compile(r"(?:6\d{5}\.SH|[03]\d{5}\.SZ)")
_COLUMNS = {"open": "开盘", "high": "最高", "low": "最低", "close": "收盘", "volume": "成交量", "amount": "成交额"}
_FIELDS = ("symbol", "bar_time", "trade_date") + tuple(_COLUMNS) + ("currency",)


def _fetch(function, kwargs, *, context):
    context.begin_operation()
    # Do not forward credential environment variables to the optional SDK process.
    allowed_env = {"PATH", "SYSTEMROOT", "TMPDIR", "LANG", "LC_ALL", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE",
                   "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy"}
    env = {key: value for key, value in os.environ.items() if key in allowed_env}
    process = None
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "ir_search.infrastructure.akshare_worker"],
            stdin=subprocess.PIPE, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(Path(__file__).resolve().parents[2]), env=env,
        )
        payload = json.dumps({"function": function, "kwargs": kwargs})
        while True:
            context.check_active()
            try:
                stdout, _ = process.communicate(input=payload, timeout=min(0.1, context.remaining_seconds()))
                break
            except subprocess.TimeoutExpired:
                # communicate retains partial output and resumes without resending stdin.
                payload = None
        returncode = process.returncode
    except OSError:
        raise DataAdapterError("dependency_missing") from None
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.communicate()
    context.check_active()
    try:
        if returncode or len(stdout) > 4000000:
            raise ValueError()
        payload = json.loads(stdout)
        if "error" in payload:
            raise DataAdapterError(payload["error"])
        rows = payload["records"]
        if not isinstance(rows, list) or len(rows) > 10000:
            raise ValueError()
        return rows
    except DataAdapterError:
        raise
    except Exception:
        raise DataAdapterError("upstream_schema") from None


class AKShareIntradayAdapter:
    name = "akshare"

    def __init__(self, *, fetch=None, now=None, backend="eastmoney"):
        if backend not in {"eastmoney", "sina"}:
            raise ValueError("Unsupported AKShare stock backend")
        self._backend = backend
        self._columns = _COLUMNS if backend == "eastmoney" else {name: name for name in ("open", "high", "low", "close")}
        self._fields = ("symbol", "bar_time", "trade_date") + tuple(self._columns) + ("currency",)
        self._fetch = fetch or _fetch
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.capabilities = (DataCapability(
            self.name, "prices_intraday", self._fields, ("A_SHARE",), frequencies=_PERIODS,
            adapter_mode=AdapterMode.LIVE,
            coverage_notes=("SH/SZ ordinary A-shares only; Beijing and derivatives not implemented here",
                            "Eastmoney: 1m approximately latest 5 trading days; Sina: up to 1970 recent bars",
                            "Sina backend exposes OHLC only; volume and turnover units are not verified",
                            "Selected backend: " + backend + "; no implicit endpoint switching",
                            "Maximum request lookback is 14 calendar days; this is not a coverage guarantee",
                            "Delayed web bars; full-session coverage and closed-bar status are unverified"),
        ),)

    def query_data(self, request, *, context):
        """Read bounded recent bars, preserving timestamps and visible data gaps."""
        if (request.dataset != "prices_intraday" or request.market != "A_SHARE" or request.frequency not in _PERIODS
                or request.adjustment != "raw" or request.value_kind.value != "actual" or request.as_of or request.cursor
                or not request.start or not 1 <= len(request.symbols) <= 5 or context.account_scope != "default"
                or any(not _SYMBOL.fullmatch(symbol) for symbol in request.symbols)):
            raise DataAdapterError("unsupported")
        fields = (set(request.fields) if request.fields else set(self._fields)) | {"symbol", "bar_time", "trade_date", "currency"}
        if not fields <= set(self._fields):
            raise DataAdapterError("unsupported")
        now = self._now().astimezone(_CN)
        earliest = now.date() - timedelta(days=RECENT_INTRADAY_DAYS - 1)
        if request.start < earliest:
            raise DataAdapterError("historical_intraday_source_not_configured")
        if request.end > now.date():
            raise DataAdapterError("unsupported")
        records, issues, seen = [], set(), set()
        for symbol in request.symbols:
            context.check_active()
            kwargs = {
                "symbol": symbol.split(".")[0], "period": request.frequency[:-1], "adjust": "",
                "start_date": f"{earliest.isoformat()} 00:00:00", "end_date": f"{now.date().isoformat()} 23:59:59",
            }
            function, time_column = "stock_zh_a_hist_min_em", "时间"
            if self._backend == "sina":
                function, time_column = "stock_zh_a_minute", "day"
                kwargs = {"symbol": symbol[-2:].lower() + symbol[:6], "period": request.frequency[:-1], "adjust": ""}
            rows = self._fetch(function, kwargs, context=context)
            context.check_active()
            if not isinstance(rows, list) or len(rows) > 10000:
                raise DataAdapterError("upstream_schema")
            included = 0
            for raw in rows:
                if not isinstance(raw, dict) or time_column not in raw or not set(self._columns.values()) <= set(raw):
                    raise DataAdapterError("upstream_schema")
                try:
                    instant = datetime.fromisoformat(raw[time_column])
                    instant = instant.replace(tzinfo=_CN) if instant.tzinfo is None else instant.astimezone(_CN)
                except (ValueError, TypeError):
                    raise DataAdapterError("upstream_schema") from None
                if not request.start <= instant.date() <= request.end:
                    continue
                if instant > now:
                    if instant <= now + timedelta(minutes=int(request.frequency[:-1])):
                        issues.add('unclosed_bar_excluded')
                        continue
                    raise DataAdapterError("upstream_schema")
                key = (symbol, instant)
                if key in seen:
                    raise DataAdapterError("upstream_schema")
                seen.add(key)
                row = {"symbol": symbol, "bar_time": instant, "trade_date": instant.date(), "currency": "CNY"}
                for name, column in self._columns.items():
                    if name not in fields:
                        continue
                    value = raw[column]
                    try:
                        if isinstance(value, bool):
                            raise ValueError()
                        value = Decimal(str(value)) if value is not None else None
                    except (InvalidOperation, ValueError):
                        raise DataAdapterError("upstream_schema") from None
                    if value is not None and not value.is_finite():
                        value = None
                    if value is not None and value < 0:
                        raise DataAdapterError("upstream_schema")
                    if value == 0 and name in {"open", "high", "low", "close"}:
                        issues.add("zero_source_price_replaced_with_null")
                        value = None
                    if value is None:
                        issues.add("missing_source_values")
                    if value is not None and name == "volume":
                        value *= 100  # Eastmoney docs: lots; this adapter accepts ordinary A-shares only.
                    row[name] = value
                records.append(row)
                included += 1
            if not included:
                issues.add("no_intraday_rows_for_requested_symbol")
            elif max(row['trade_date'] for row in records if row['symbol'] == symbol) < request.end:
                issues.add('requested_end_date_without_bars')
        records.sort(key=lambda row: (row["symbol"], row["bar_time"]))
        if len(records) > request.limit:
            issues.add("row_limit_reached_narrow_request")
        issues.update({"recent_intraday_coverage_unverified", "web_quote_delay_unverified", "live_bar_may_be_incomplete"})
        if self._backend == "sina":
            issues.add("sina_activity_units_not_exposed")
        publisher = "Eastmoney via AKShare" if self._backend == "eastmoney" else "Sina via AKShare"
        return DataPage(records[:request.limit], Provenance(self.name, publisher, self._now(),
                        authority=SourceAuthority.DATA_VENDOR, adapter_mode=AdapterMode.LIVE), complete=False,
                        diagnostics=[Diagnostic(code, "query_data", provider=self.name, adapter_mode=AdapterMode.LIVE)
                                     for code in sorted(issues)])
