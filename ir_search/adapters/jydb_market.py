"""JYDB raw A-share daily prices; mappings verified against the local schema dictionary."""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone

from ir_search.contracts import AdapterMode, DataCapability, DataPage, Diagnostic, Provenance
from ir_search.infrastructure.mysql import _select
from ir_search.infrastructure.pagination import _decode_cursor, _encode_cursor
from ir_search.infrastructure.quote_quality import _normalize_a_share_zero_prices
from ir_search.models import FailureKind, SourceAuthority
from ir_search.registry import DataAdapterError

_MARKETS = {"SH": 83, "SZ": 90, "BJ": 18}
_SYMBOL = re.compile(r"\d{6}\.(SH|SZ|BJ)")
_COLUMNS = {"trade_date": "TradingDay", "open": "OpenPrice", "high": "HighPrice", "low": "LowPrice",
            "close": "ClosePrice", "volume": "TurnoverVolume", "amount": "TurnoverValue"}
_FIELDS = ("symbol",) + tuple(_COLUMNS) + ("currency",)


class JYDBMarketAdapter:
    name = "jydb"

    def __init__(self, profile, *, select=None):
        if profile.provider != self.name:
            raise ValueError("Wrong provider profile")
        self._profile = profile
        self._select = select or _select
        self.capabilities = (DataCapability(
            self.name, "prices_daily", _FIELDS, ("A_SHARE",), supports_pagination=True,
            adapter_mode=AdapterMode.LIVE,
            coverage_notes=("A-share category 1 only; STAR board uses LC_STIBDailyQuote",
                            "Raw shares/CNY; no adjustment or point-in-time guarantee",
                            "Table rows, not a verified full exchange calendar; after-hours STAR table excluded"),
        ),)

    def query_data(self, request, *, context):
        """Resolve InnerCode, read up to two single-table pages, normalize and paginate."""
        if (request.dataset != "prices_daily" or request.market != "A_SHARE" or request.frequency != "1d"
                or request.adjustment != "raw" or request.value_kind.value != "actual" or request.as_of
                or not request.symbols or not request.start or request.end == date.max
                or context.account_scope != "default" or any(not _SYMBOL.fullmatch(s) for s in request.symbols)):
            raise DataAdapterError("unsupported")
        fields = (set(request.fields) if request.fields else set(_FIELDS)) | {"symbol", "trade_date", "currency"}
        if not fields <= set(_FIELDS):
            raise DataAdapterError("unsupported")
        last = _decode_cursor(request, self._profile)
        if last is not None:
            try:
                if not isinstance(last, list) or len(last) != 2 or type(last[0]) is not int or last[0] <= 0:
                    raise ValueError()
                last = (last[0], datetime.fromisoformat(last[1]))
                if last[1].tzinfo or last[1].time() != time():
                    raise ValueError()
            except Exception:
                raise DataAdapterError("invalid_cursor") from None
        conditions, params = [], []
        for symbol in request.symbols:
            code, venue = symbol.split(".")
            conditions.append("(SecuCode = %s AND SecuMarket = %s)")
            params.extend([code, _MARKETS[venue]])
        sql = ("SELECT InnerCode, SecuCode, SecuMarket, ListedSector FROM SecuMain WHERE SecuCategory = 1 AND ("
               + " OR ".join(conditions) + ") ORDER BY InnerCode LIMIT %s")
        mapped = self._select(self._profile, sql, tuple(params + [201]), max_rows=201, context=context)
        if len(mapped) > 200:
            raise DataAdapterError("upstream_schema")
        codes, symbols = {}, set()
        groups = {"QT_DailyQuote": [], "LC_STIBDailyQuote": []}
        for row in mapped:
            venue = next((key for key, value in _MARKETS.items() if value == row.get("SecuMarket")), None)
            symbol = f"{row.get('SecuCode')}.{venue}"
            inner, sector = row.get("InnerCode"), row.get("ListedSector")
            if (symbol not in request.symbols or symbol in symbols or type(inner) is not int
                    or not 0 < inner < 2 ** 63 or inner in codes or type(sector) is not int
                    or sector not in {1, 2, 6, 7, 8}):
                raise DataAdapterError("upstream_schema")
            symbols.add(symbol)
            codes[inner] = symbol
            groups["LC_STIBDailyQuote" if sector == 7 else "QT_DailyQuote"].append(inner)
        if not codes:
            raise DataAdapterError("not_found")
        columns = "InnerCode, " + ", ".join(f"{column} AS `{name}`" for name, column in _COLUMNS.items() if name in fields)
        collected = []
        issues = {"database_snapshot_not_pit"}
        for table, identities in groups.items():
            if not identities:
                continue
            conditions = ["InnerCode IN (" + ",".join(["%s"] * len(identities)) + ")", "TradingDay >= %s", "TradingDay < %s"]
            params = sorted(identities) + [datetime.combine(request.start, time()), datetime.combine(request.end + timedelta(days=1), time())]
            if last:
                conditions.append("(InnerCode, TradingDay) > (%s, %s)")
                params.extend(last)
            sql = f"SELECT {columns} FROM {table} WHERE " + " AND ".join(conditions) + " ORDER BY InnerCode, TradingDay LIMIT %s"
            rows = self._select(self._profile, sql, tuple(params + [request.limit + 1]), max_rows=request.limit + 1, context=context)
            for row in rows:
                inner, day = row.get("InnerCode"), row.get("trade_date")
                if isinstance(day, datetime) and (day.tzinfo is not None or day.time() != time()):
                    raise DataAdapterError("upstream_schema")
                if isinstance(day, datetime):
                    day = day.date()
                if type(inner) is not int or inner not in identities or type(day) is not date or not request.start <= day <= request.end:
                    raise DataAdapterError("upstream_schema")
                key = (inner, datetime.combine(day, time()))
                if last and key <= last:
                    raise DataAdapterError("upstream_schema")
                normalized = {name: row[name] for name in fields if name in _COLUMNS}
                normalized.update(symbol=codes[inner], trade_date=day, currency="CNY")
                _normalize_a_share_zero_prices(normalized, issues)
                collected.append((key, normalized))
        collected.sort(key=lambda item: item[0])
        if len({key for key, _ in collected}) != len(collected):
            raise DataAdapterError("upstream_schema")
        more = len(collected) > request.limit
        collected = collected[:request.limit]
        cursor = None
        if more:
            identity, timestamp = collected[-1][0]
            cursor = _encode_cursor(request, self._profile, [identity, timestamp.isoformat()])
        diagnostics = [Diagnostic(code, "query_data", provider=self.name, adapter_mode=AdapterMode.LIVE) for code in sorted(issues)]
        missing = symbols != set(request.symbols)
        if missing:
            diagnostics.append(Diagnostic("security_mapping_incomplete", "query_data", provider=self.name,
                                          failure_kind=FailureKind.UPSTREAM_SCHEMA, adapter_mode=AdapterMode.LIVE))
        return DataPage([row for _, row in collected], Provenance(self.name, "JYDB", datetime.now(timezone.utc),
                        authority=SourceAuthority.DATA_VENDOR, adapter_mode=AdapterMode.LIVE),
                        complete=not more and not missing, next_cursor=cursor, diagnostics=diagnostics)
