"""Wind MySQL data adapter: A-share directory and raw daily prices."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import Decimal

from ir_search.contracts import AdapterMode, DataCapability, DataPage, Diagnostic, Provenance
from ir_search.infrastructure.mysql import _select
from ir_search.infrastructure.pagination import _decode_cursor, _encode_cursor
from ir_search.infrastructure.quote_quality import _normalize_a_share_zero_prices
from ir_search.models import SourceAuthority
from ir_search.registry import DataAdapterError

_DIRECTORY = {"symbol": "S_INFO_WINDCODE", "name": "S_INFO_NAME", "exchange": "S_INFO_EXCHMARKET", "currency": "CRNCY_CODE"}
_PRICES = {"symbol": "S_INFO_WINDCODE", "trade_date": "TRADE_DT", "open": "S_DQ_OPEN", "high": "S_DQ_HIGH",
           "low": "S_DQ_LOW", "close": "S_DQ_CLOSE", "volume": "S_DQ_VOLUME", "amount": "S_DQ_AMOUNT", "currency": "CRNCY_CODE"}
_SYMBOL = re.compile(r"\d{6}\.(SH|SZ|BJ)")


class WindMySQLAdapter:
    name = "wind_mysql"

    def __init__(self, profile, *, select=None):
        if profile.provider != self.name:
            raise ValueError("Wrong provider profile")
        self._profile = profile
        self._select = select or _select
        self.capabilities = (
            DataCapability(self.name, "securities", tuple(_DIRECTORY) + ("market",), ("A_SHARE",),
                           frequencies=("snapshot",), adjustments=("none",), supports_pagination=True, adapter_mode=AdapterMode.LIVE),
            DataCapability(self.name, "prices_daily", tuple(_PRICES), ("A_SHARE",),
                           supports_pagination=True, adapter_mode=AdapterMode.LIVE),
        )

    def query_data(self, request, *, context):
        """Issue one parameterized single-table page; retain keys and explicit units."""
        prices = request.dataset == "prices_daily"
        if (request.dataset not in {"securities", "prices_daily"} or request.market != "A_SHARE"
                or request.value_kind.value != "actual" or request.as_of
                or request.adjustment != ("raw" if prices else "none")
                or request.frequency != ("1d" if prices else "snapshot")
                or (prices and (not request.symbols or not request.start))
                or (not prices and request.start) or context.account_scope != "default"
                or any(not _SYMBOL.fullmatch(s) for s in request.symbols)):
            raise DataAdapterError("unsupported")
        mapping = _PRICES if prices else _DIRECTORY
        fields = set(request.fields) if request.fields else set(mapping) | ({"market"} if not prices else set())
        fields |= {"symbol", "currency"} | ({"trade_date"} if prices else set())
        if not fields <= set(mapping) | ({"market"} if not prices else set()):
            raise DataAdapterError("unsupported")
        for name in ("volume", "amount"):
            if name in fields and getattr(self._profile, name + "_multiplier") is None:
                raise DataAdapterError("units_not_configured")
        columns = ", ".join(f"{column} AS `{name}`" for name, column in mapping.items() if name in fields)
        conditions, params = [], []
        if request.symbols:
            conditions.append("S_INFO_WINDCODE IN (" + ",".join(["%s"] * len(request.symbols)) + ")")
            params.extend(request.symbols)
        if prices:
            conditions.append("TRADE_DT BETWEEN %s AND %s")
            params.extend([request.start.strftime("%Y%m%d"), request.end.strftime("%Y%m%d")])
        last = _decode_cursor(request, self._profile)
        if last is not None:
            if (not isinstance(last, list) or len(last) != (2 if prices else 1)
                    or not isinstance(last[0], str) or not _SYMBOL.fullmatch(last[0])):
                raise DataAdapterError("invalid_cursor")
            if prices:
                if not isinstance(last[1], str) or not re.fullmatch(r"\d{8}", last[1]):
                    raise DataAdapterError("invalid_cursor")
                conditions.append("(S_INFO_WINDCODE, TRADE_DT) > (%s, %s)")
            else:
                conditions.append("S_INFO_WINDCODE > %s")
            params.extend(last)
        table = "ashareeodprices" if prices else "asharedescription"
        order = "S_INFO_WINDCODE, TRADE_DT" if prices else "S_INFO_WINDCODE"
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        sql = f"SELECT {columns} FROM {table}{where} ORDER BY {order} LIMIT %s"
        rows = self._select(self._profile, sql, tuple(params + [request.limit + 1]), max_rows=request.limit + 1, context=context)
        more = len(rows) > request.limit
        rows = rows[:request.limit]
        records = []
        issues = {"database_snapshot_not_pit"}
        if self._profile.tls_mode == "disabled":
            issues.add("non_tls_explicitly_configured")
        cursor_key = None
        for original in rows:
            row = dict(original)
            if not isinstance(row.get("symbol"), str) or not _SYMBOL.fullmatch(row["symbol"]):
                raise DataAdapterError("upstream_schema")
            cursor_key = [row["symbol"]]
            if prices:
                raw = row.get("trade_date")
                if isinstance(raw, datetime):
                    raw = raw.date()
                if isinstance(raw, date):
                    raw = raw.strftime("%Y%m%d")
                if not isinstance(raw, str) or not re.fullmatch(r"\d{8}", raw):
                    raise DataAdapterError("upstream_schema")
                row["trade_date"] = datetime.strptime(raw, "%Y%m%d").date()
                cursor_key.append(raw)
                _normalize_a_share_zero_prices(row, issues)
                for name in ("volume", "amount"):
                    if name in row and row[name] is not None:
                        if isinstance(row[name], bool) or not isinstance(row[name], (int, float, Decimal)):
                            raise DataAdapterError("upstream_schema")
                        row[name] = Decimal(str(row[name])) * getattr(self._profile, name + "_multiplier")
            elif "market" in fields:
                row["market"] = "A_SHARE"
            records.append(row)
        provenance = Provenance(self.name, "Wind", datetime.now(timezone.utc), authority=SourceAuthority.DATA_VENDOR,
                                adapter_mode=AdapterMode.LIVE)
        return DataPage(records, provenance, complete=not more,
                        next_cursor=_encode_cursor(request, self._profile, cursor_key) if more else None,
                        diagnostics=[Diagnostic(code, "query_data", provider=self.name,
                                                adapter_mode=AdapterMode.LIVE) for code in sorted(issues)])
