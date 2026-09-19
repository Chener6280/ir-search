from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from ir_search import DataRegistry, DataRequest, RequestContext, get_data
from ir_search.adapters.akshare_futures import AKShareFuturesAdapter
from ir_search.infrastructure.calendars import _future_calendars, _resolve_bar_date
from ir_search.registry import DataAdapterError


@pytest.mark.parametrize('stamp,expected', [
    ('2026-09-11T21:05:00+08:00', '2026-09-14'),
    ('2026-09-12T00:30:00+08:00', '2026-09-14'),
    ('2026-09-11T10:00:00+08:00', '2026-09-11'),
    ('2026-09-12T21:05:00+08:00', None),
    ('2026-09-13T00:30:00+08:00', None),
    ('2026-09-11T18:00:00+08:00', None),
    ('2026-09-30T21:05:00+08:00', '2026-10-08'),
])
def test_night_date_uses_published_open_dates_including_weekend_and_holiday(stamp, expected):
    dates = {date.fromisoformat(s) for s in ['2026-09-11','2026-09-14','2026-09-30','2026-10-08']}
    actual = _resolve_bar_date(datetime.fromisoformat(stamp), dates)
    assert (actual.isoformat() if actual else None) == expected
    assert _resolve_bar_date(datetime.fromisoformat(stamp), set()) is None


def test_calendar_sql_binds_contract_lifetime_and_returns_per_venue_dates(monkeypatch):
    calls = []
    def select(profile, sql, params, **kw):
        calls.append((sql, params))
        if 'cfuturesdescription' in sql:
            return [{'S_INFO_CODE':'RB2610', 'S_INFO_EXCHMARKET':'SHFE'}]
        return [{'S_INFO_EXCHMARKET':'SHFE', 'TRADE_DAYS':s} for s in ['20260911','20260914']]
    monkeypatch.setattr('ir_search.infrastructure.calendars._select', select)
    dates, missing = _future_calendars(None, ('RB2610','UNKNOWN'), date(2026,9,11), date(2026,9,12), RequestContext())
    assert missing == {'UNKNOWN'} and dates['RB2610'] == {date(2026,9,11),date(2026,9,14)}
    assert calls[0][1] == ('RB2610','UNKNOWN','20260912','20260911','1',41)
    assert calls[1][1] == ('SHFE','20260910','20260926',1001)


@pytest.mark.parametrize('failure', [False, True])
def test_futures_calendar_enrichment_is_visible_and_failure_keeps_observations(failure):
    def calendars(*a):
        if failure: raise DataAdapterError('network')
        return {'RB2610': {date(2026,9,11),date(2026,9,14)}}, set()
    rows = [{'datetime':'2026-09-11 21:05:00','open':3,'high':4,'low':2,'close':3,'volume':1,'hold':2}]
    adapter = AKShareFuturesAdapter(fetch=lambda *a, **k: rows, now=lambda: datetime(2026,9,14,tzinfo=timezone.utc),
        calendar_profile=SimpleNamespace(tls_mode='disabled'), calendars=calendars)
    registry = DataRegistry(); registry.register(adapter)
    result = get_data(DataRequest('futures_intraday', market='CN_FUTURES', symbols=('RB2610',),
        start='2026-09-11', end='2026-09-11'), registry=registry)
    assert result.status.value == 'partial'
    assert result.records[0]['trade_date'] == (None if failure else date(2026,9,14))
    codes = {d.code for d in result.diagnostics}
    assert ('wind_calendar_network' if failure else 'calendar_non_tls_explicitly_configured') in codes
    assert ('night_date_resolved_from_published_calendar' in codes) is not failure
