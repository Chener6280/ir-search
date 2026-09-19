"""A-share source conventions shared by the independent database adapters."""
from __future__ import annotations


def _normalize_a_share_zero_prices(row, issues):
    # Halted/non-trading source rows may use zero for absent OHLC observations.
    # Keep zero activity, but never present a zero stock price as an executed trade.
    for name in ("open", "high", "low", "close"):
        value = row.get(name)
        if value is not None and not isinstance(value, bool) and value == 0:
            row[name] = None
            issues.add("zero_source_price_replaced_with_null")
