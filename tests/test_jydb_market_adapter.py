from dataclasses import replace
from datetime import datetime
from decimal import Decimal
import json

import pytest

from ir_search import DataRequest, DataRegistry, get_data
from ir_search.adapters.jydb_market import JYDBMarketAdapter
from ir_search.infrastructure.credentials import MySQLProfile

PROFILE = MySQLProfile("jydb", "host", "db", "user", "must_not_escape")


def request(**kwargs):
    return DataRequest("prices_daily", symbols=["000001.SZ"], start="2026-09-01", end="2026-09-03", **kwargs)


def price(inner=1, day=1):
    return dict(InnerCode=inner, trade_date=datetime(2026, 9, day), open=10, high=12, low=9,
                close=Decimal("11.01"), volume=Decimal("1000"), amount=Decimal("11010"))


class Database:
    def __init__(self):
        self.calls = []
        self.securities = [dict(InnerCode=1, SecuCode="000001", SecuMarket=90, ListedSector=1)]
        self.rows = {"QT_DailyQuote": [price()], "LC_STIBDailyQuote": []}

    def select(self, profile, sql, params, **kwargs):
        self.calls.append((sql, params, kwargs))
        if "FROM SecuMain" in sql:
            return self.securities
        table = "LC_STIBDailyQuote" if "FROM LC_STIBDailyQuote" in sql else "QT_DailyQuote"
        return self.rows[table]

    def run(self, req=None):
        registry = DataRegistry()
        registry.register(JYDBMarketAdapter(PROFILE, select=self.select))
        return get_data(req or request(), registry=registry)


def test_normalizes_verified_share_yuan_units_and_separate_single_table_queries():
    db = Database()
    result = db.run()
    assert result.complete and result.records[0]["volume"] == Decimal(1000)
    assert result.records[0]["amount"] == Decimal(11010) and result.records[0]["currency"] == "CNY"
    assert result.provenance.authority.value == "data_vendor" and result.provenance.provider == "jydb"
    assert len(db.calls) == 2 and all("JOIN" not in sql for sql, _, _ in db.calls)
    assert db.calls[0][1] == ("000001", 90, 201)
    assert "000001" not in db.calls[0][0]
    assert "must_not_escape" not in json.dumps(result.to_dict())


def test_star_board_uses_listed_sector_not_code_guess_and_cursor_merges_table_pages():
    db = Database()
    db.securities.append(dict(InnerCode=2, SecuCode="688001", SecuMarket=83, ListedSector=7))
    db.rows["LC_STIBDailyQuote"] = [price(2)]
    req = replace(request(limit=1), symbols=["000001.SZ", "688001.SH"])
    result = db.run(req)
    assert result.next_cursor and not result.complete and result.records[0]["symbol"] == "000001.SZ"
    assert len(db.calls) == 3 and "LC_STIBDailyQuote" in db.calls[-1][0]
    db.rows["QT_DailyQuote"] = []
    result2 = db.run(replace(req, provider="jydb", cursor=result.next_cursor))
    assert result2.complete and result2.records[0]["symbol"] == "688001.SH"
    assert db.calls[-1][1][-3:] == (1, datetime(2026, 9, 1), 2)
    db.calls.clear()
    bad = db.run(replace(req, provider="jydb", cursor=result.next_cursor, end="2026-09-02"))
    assert bad.diagnostics[-1].code == "invalid_cursor" and not db.calls


def test_projection_and_missing_security_are_explicit():
    db = Database()
    db.rows["QT_DailyQuote"] = [{key: value for key, value in price().items() if key in {"InnerCode", "trade_date", "close"}}]
    result = db.run(replace(request(fields=["close"]), symbols=["000001.SZ", "600519.SH"]))
    assert result.status.value == "partial" and result.records[0]["close"] == Decimal("11.01")
    assert "TurnoverVolume" not in db.calls[-1][0]
    assert "security_mapping_incomplete" in [d.code for d in result.diagnostics]
    assert db.run(replace(result.request, allow_partial=False)).records == []


@pytest.mark.parametrize("mutation", [
    lambda db: db.securities.append(dict(db.securities[0])),
    lambda db: db.securities[0].update(ListedSector=None),
    lambda db: db.securities[0].update(InnerCode=True),
    lambda db: db.rows["QT_DailyQuote"][0].update(InnerCode=2),
    lambda db: db.rows["QT_DailyQuote"][0].update(trade_date=datetime(2026, 9, 1, 1)),
    lambda db: db.rows["QT_DailyQuote"][0].update(volume=float("nan")),
    lambda db: db.rows["QT_DailyQuote"].append(price()),
])
def test_bad_mapping_or_rows_fail_without_guessing(mutation):
    db = Database()
    mutation(db)
    result = db.run()
    assert result.status.value == "error" and not result.records


def test_empty_mapping_and_unsupported_inputs_do_not_query_quote_tables():
    db = Database()
    db.securities = []
    result = db.run()
    assert result.status.value == "unavailable" and len(db.calls) == 1
    for req in [replace(request(), symbols=["bad"]), request(adjustment="forward"), request(market="CN_OPTIONS")]:
        db.calls.clear()
        assert not db.run(req).records and not db.calls
    with pytest.raises(ValueError):
        JYDBMarketAdapter(replace(PROFILE, provider="wind_mysql"))
