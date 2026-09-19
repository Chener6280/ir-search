"""Current-day ETF option point observations, without fabricated OHLC history."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from ir_search.contracts import AdapterMode, DataCapability, DataPage, Diagnostic, Provenance
from ir_search.contracts.market import MARKET_DATASETS
from ir_search.models import SourceAuthority
from ir_search.registry import DataAdapterError
from .akshare_intraday import _fetch, _CN
from ._market_common import _number, _selected

_COLUMNS={'price':'价格','average_price':'均价','source_volume':'成交','source_open_interest':'持仓'}


class AKShareOptionsAdapter:
    name='akshare'

    def __init__(self, *, fetch=None, now=None):
        self._fetch=fetch or _fetch
        self._now=now or (lambda:datetime.now(timezone.utc))
        self.capabilities=(DataCapability(self.name,'options_intraday',
            tuple(f.name for f in MARKET_DATASETS['options_intraday'].fields),('CN_OPTIONS',),frequencies=('1m',),
            adapter_mode=AdapterMode.LIVE,coverage_notes=('Sina ETF option minute point prices, current day only; no OHLC',
                'Eight-digit SSE/SZSE contract codes; index/commodity option intraday not covered by this adapter',
                'Source volume/open interest counts unverified; no full-session or latency guarantee')),)

    def query_data(self,request,*,context):
        """Read only the available current day; stale dates and duplicates are rejected."""
        now=self._now().astimezone(_CN)
        if (request.dataset!='options_intraday' or request.market!='CN_OPTIONS' or request.frequency!='1m'
                or request.adjustment!='raw' or request.value_kind.value!='actual' or request.as_of or request.cursor
                or not request.start or not 1<=len(request.symbols)<=5 or context.account_scope!='default'
                or any(not re.fullmatch(r'\d{8}\.(SH|SZ)',s) for s in request.symbols)):
            raise DataAdapterError('unsupported')
        if request.start!=now.date() or request.end!=now.date():
            raise DataAdapterError('current_day_only')
        issues={'point_price_not_ohlc','current_day_only','activity_counting_convention_unverified',
                'recent_intraday_coverage_unverified','web_quote_delay_unverified','live_bar_may_be_incomplete'}
        records,seen=[],set()
        for symbol in request.symbols:
            rows=self._fetch('option_sse_minute_sina',{'symbol':symbol.split('.')[0]},context=context)
            if not isinstance(rows,list) or len(rows)>10000:raise DataAdapterError('upstream_schema')
            if not rows:issues.add('no_intraday_rows_for_requested_symbol')
            for raw in rows:
                try:
                    day=raw['日期'].split('T')[0]
                    stamp=datetime.fromisoformat(day+'T'+raw['时间']).replace(tzinfo=_CN)
                except (KeyError,TypeError,ValueError,AttributeError):
                    raise DataAdapterError('upstream_schema') from None
                if stamp.date()!=now.date():raise DataAdapterError('stale_intraday_data')
                if stamp>now or (symbol,stamp) in seen:raise DataAdapterError('upstream_schema')
                seen.add((symbol,stamp))
                row={'symbol':symbol,'bar_time':stamp,'trade_date':stamp.date(),'currency':'CNY'}
                for name,column in _COLUMNS.items():
                    value=_number(raw[column])
                    if value is not None and value<0:raise DataAdapterError('upstream_schema')
                    if name in ('price','average_price') and value==0:
                        value=None;issues.add('zero_source_price_replaced_with_null')
                    row[name]=value
                records.append({k:v for k,v in row.items() if k in _selected(request)})
        records.sort(key=lambda r:(r['symbol'],r['bar_time']))
        if len(records)>request.limit:issues.add('row_limit_reached_narrow_request')
        return DataPage(records[:request.limit],Provenance(self.name,'Sina via AKShare',self._now(),
            authority=SourceAuthority.DATA_VENDOR,adapter_mode=AdapterMode.LIVE),complete=False,
            diagnostics=[Diagnostic(code,'query_data',provider=self.name,adapter_mode=AdapterMode.LIVE) for code in sorted(issues)])
