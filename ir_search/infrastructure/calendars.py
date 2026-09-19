"""Resolve observed futures timestamps using published open dates, never weekdays alone."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .mysql import _select
from ir_search.registry import DataAdapterError

_CN=ZoneInfo('Asia/Shanghai')


def _future_calendars(profile,symbols,start,end,context):
    horizon=end+timedelta(days=14)
    rows=_select(profile,
        'SELECT S_INFO_CODE, S_INFO_EXCHMARKET FROM cfuturesdescription WHERE S_INFO_CODE IN ('+','.join(['%s']*len(symbols))+') AND S_INFO_LISTDATE <= %s AND S_INFO_DELISTDATE >= %s AND FS_INFO_TYPE = %s ORDER BY S_INFO_CODE LIMIT %s',
        tuple(symbols)+(end.strftime('%Y%m%d'),start.strftime('%Y%m%d'),'1',41),max_rows=41,context=context)
    venues={}
    for row in rows:
        code,venue=row['S_INFO_CODE'],row['S_INFO_EXCHMARKET']
        if code not in symbols or code in venues or venue not in {'CFFEX','SHFE','DCE','CZCE','INE','GFEX'}:
            raise DataAdapterError('upstream_schema')
        venues[code]=venue
    if not venues:return {},set(symbols)
    markets=sorted(set(venues.values()))
    rows=_select(profile,
        'SELECT S_INFO_EXCHMARKET, TRADE_DAYS FROM cfuturescalendar WHERE S_INFO_EXCHMARKET IN ('+','.join(['%s']*len(markets))+') AND TRADE_DAYS BETWEEN %s AND %s ORDER BY S_INFO_EXCHMARKET, TRADE_DAYS LIMIT %s',
        tuple(markets)+((start-timedelta(days=1)).strftime('%Y%m%d'),horizon.strftime('%Y%m%d'),1001),max_rows=1001,context=context)
    if len(rows)>1000:raise DataAdapterError('upstream_schema')
    dates={venue:set() for venue in markets}
    for row in rows:
        venue=row['S_INFO_EXCHMARKET']
        if venue not in dates:raise DataAdapterError('upstream_schema')
        try:day=datetime.strptime(row['TRADE_DAYS'],'%Y%m%d').date()
        except (TypeError,ValueError):raise DataAdapterError('upstream_schema') from None
        dates[venue].add(day)
    return {code:dates[venue] for code,venue in venues.items()},set(symbols)-set(venues)


def _resolve_bar_date(stamp,open_dates):
    local=stamp.astimezone(_CN);day=local.date()
    if local.hour>=21:origin=day
    elif local.hour<6:origin=day-timedelta(days=1)
    else:return day if day in open_dates and 8<=local.hour<=16 else None
    if origin not in open_dates:return None
    return next((candidate for candidate in sorted(open_dates) if candidate>origin),None)
