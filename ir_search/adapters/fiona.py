"""Formal Fiona datasets on the dedicated Bearer client; source uncertainty is explicit."""
from datetime import date, datetime, timezone, timedelta
import json,re
from ir_search.contracts import AdapterMode, DataCapability, DataPage, Diagnostic, Provenance
from ir_search.contracts.expanded_data import FIONA_DATASETS
from ir_search.contracts.market import MARKET_DATASETS
from ir_search.infrastructure.fiona import FionaClient
from ir_search.models import FailureKind, SourceAuthority
from ir_search.registry import DataAdapterError
from ._market_common import _day,_number,_selected
from .domestic_derivatives import _parse_symbol

_EXCHANGES={'SH':'XSHG','SZ':'XSHE','SHF':'SHFE','DCE':'DCE','CZC':'CZCE','INE':'INE','CFE':'CFFEX','GFE':'GFEX'}


def _payload(response):
    data=response.result.get('structuredContent')
    if data is None:
        try:data=json.loads('\n'.join(v['text'] for v in response.result['content'] if v.get('type')=='text'))
        except (ValueError,TypeError,KeyError):raise DataAdapterError('upstream_schema') from None
    if not isinstance(data,dict):raise DataAdapterError('upstream_schema')
    items=data.get('items')
    if (response.tool=='get_option_contract_info' and isinstance(items,dict) and data.get('total') is None
            and data.get('returned') is None and data.get('truncated') is False):
        return [items],False
    if items is None:
        cols,rows=data.get('columns'),data.get('rows')
        if (not isinstance(cols,list) or any(not isinstance(c,str) for c in cols) or len(set(cols))!=len(cols)
                or not isinstance(rows,list) or any(not isinstance(r,list) or len(r)!=len(cols) for r in rows)):
            raise DataAdapterError('upstream_schema')
        items=[dict(zip(cols,r)) for r in rows]
    if not isinstance(items,list) or len(items)>500 or any(not isinstance(r,dict) for r in items):raise DataAdapterError('upstream_schema')
    if (type(data.get('total')) is not int or type(data.get('returned')) is not int or type(data.get('truncated')) is not bool
            or data['returned']!=len(items) or data['total']<len(items)):raise DataAdapterError('upstream_schema')
    return items,data['truncated'] or data['total']>len(items)


def _date(value):
    if isinstance(value,str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}T00:00:00',value):return date.fromisoformat(value[:10])
    return _day(value)


class FionaAdapter:
    name='fiona'
    def __init__(self,profile,*,client_factory=None):
        self._profile,self._factory=profile,client_factory or FionaClient
        self.capabilities=tuple(DataCapability(self.name,d,tuple(f.name for f in definition.fields),
            ('CN_FUTURES','CN_OPTIONS') if d=='derivatives_bars' else ('CN_FUTURES',) if d=='futures_daily' else ('CN_OPTIONS',),
            frequencies=('1m','5m','15m','30m','60m') if d=='derivatives_bars' else ('1d',),
            adjustments=('none',) if d in FIONA_DATASETS else ('raw',),adapter_mode=AdapterMode.LIVE,
            coverage_notes=('Explicit source; does not replace domestic Wind/JYDB EOD or AKShare intraday routes.',
                'Concrete four-digit-year-month contracts or SH/SZ option codes; source exchange and lifecycle verified.',
                'Provider truncation and missing contracts explicit; narrow date/contract ranges to continue.',
                'Risk-model/scale unknown; native bars counts retained; futures EOD amount withheld until units confirmed.'))
            for d,definition in {**{k:MARKET_DATASETS[k] for k in ('futures_daily','options_daily')},**FIONA_DATASETS}.items())

    def query_data(self,request,*,context):
        """Verify contract identities, then normalize only fields whose semantics are known."""
        option=request.market=='CN_OPTIONS'
        cap=next((c for c in self.capabilities if c.dataset==request.dataset and request.market in c.markets),None)
        if (not cap or request.frequency not in cap.frequencies or request.adjustment not in cap.adjustments or request.as_of or request.cursor
            or not request.start or not 1<=len(request.symbols)<=5 or context.account_scope!='default' or request.value_kind.value!='actual'
            or (request.end-request.start).days>365):raise DataAdapterError('unsupported')
        if request.dataset=='derivatives_bars':
            today=datetime.now(timezone(timedelta(hours=8))).date()
            if request.start<today-timedelta(days=13) or request.end>today:raise DataAdapterError('historical_intraday_source_not_configured')
        native={}
        for symbol in request.symbols:
            _parse_symbol(symbol,option=option)
            code,suffix=symbol.split('.');code=code.replace('-','').upper()
            if suffix not in {'SH','SZ'} and not re.fullmatch(r'[A-Z]{1,3}\d{4}(?:[CP]\d+(?:\.\d+)?)?',code):raise DataAdapterError('unsupported')
            if code in native:raise DataAdapterError('unsupported')
            native[code]=symbol
        route='options_market_data' if option else 'futures_market_data'
        client=self._factory(self._profile,route)
        metadata,_truncated=_payload(client.read('get_option_contract_info' if option else 'get_futures_contract_info_table',
            dict(order_book_ids=','.join(native),date=request.end.isoformat(),limit=20) if option else dict(contract=','.join(native),limit=20),context=context))
        if _truncated:raise DataAdapterError('upstream_schema')
        verified={}
        for m in metadata:
            code=str(m.get('order_book_id' if option else '合约代码','')).upper()
            if code not in native:raise DataAdapterError('upstream_schema')
            suffix=native[code].split('.')[-1]
            if m.get('exchange' if option else '交易所代码')!=_EXCHANGES[suffix]:raise DataAdapterError('upstream_schema')
            lo,hi=_date(m['listed_date' if option else '上市日期']),_date(m['de_listed_date' if option else '最后交易日'])
            if code in verified or lo>hi or hi<request.start or lo>request.end:raise DataAdapterError('upstream_schema')
            verified[code]=(lo,hi)
        issues={};records=[];complete=len(verified)==len(native)
        if not complete:issues['fiona_contracts_missing']=FailureKind.UPSTREAM_SCHEMA
        if verified:
            args=dict(start_date=request.start.isoformat(),end_date=request.end.isoformat(),limit=min(500,request.limit))
            if option:
                args['order_book_ids']=','.join(verified);args['frequency']=request.frequency
                tool='get_option_risk_metrics' if request.dataset=='option_risk' else 'get_option_history_quotes'
            else:
                args.update(contract=','.join(verified),frequency=request.frequency);tool='get_futures_timeseries_quotes'
            rows,truncated=_payload(client.read(tool,args,context=context));complete=complete and not truncated
            if truncated:issues['fiona_source_truncated_narrow_request']=FailureKind.UPSTREAM_SCHEMA
            for r in rows:
                code=str(r.get('合约代码' if request.dataset=='futures_daily' else 'order_book_id','')).upper()
                if code not in verified:raise DataAdapterError('upstream_schema')
                day=_date(r['交易日期'] if request.dataset=='futures_daily' else r['date'] if request.dataset=='options_daily' else r['trading_date'])
                if not request.start<=day<=request.end or not verified[code][0]<=day<=verified[code][1]:raise DataAdapterError('upstream_schema')
                row=dict(symbol=native[code],trade_date=day)
                if request.dataset=='option_risk':
                    row.update({ 'source_'+k:_number(r.get(k)) for k in ('delta','gamma','vega','theta','rho','iv')})
                    row.update(model='provider_default_undocumented',price_type='provider_default_undocumented',source_contract=code)
                    issues['fiona_risk_model_and_units_unverified']=FailureKind.UPSTREAM_SCHEMA
                elif request.dataset=='derivatives_bars':
                    instant=datetime.fromisoformat(str(r['datetime']))
                    if instant.tzinfo is None:instant=instant.replace(tzinfo=timezone(timedelta(hours=8)))
                    if instant.utcoffset()!=timedelta(hours=8) or not 0<=(day-instant.date()).days<=7:raise DataAdapterError('upstream_schema')
                    row.update(bar_time=instant,source_contract=code,**{k:_number(r.get(k)) for k in ('open','high','low','close')})
                    row.update(source_volume=_number(r.get('volume')),source_turnover=_number(r.get('total_turnover')),source_open_interest=_number(r.get('open_interest')))
                    issues['fiona_bar_counting_and_turnover_units_unverified']=FailureKind.UPSTREAM_SCHEMA
                    issues['bar_bounds_use_exchange_trading_day']=FailureKind.NONE
                else:
                    fields={'open':'开盘价','high':'最高价','low':'最低价','close':'收盘价','settlement':'结算价',
                        'previous_settlement':'前结算价','volume':'成交量','open_interest':'持仓量'} if not option else {'open':'open','high':'high','low':'low','close':'close','settlement':'settlement','previous_settlement':'prev_settlement','volume':'volume','open_interest':'open_interest'}
                    if not option and r.get('交易所代码')!=_EXCHANGES[native[code].split('.')[-1]]:raise DataAdapterError('upstream_schema')
                    row.update({k:_number(r.get(v)) for k,v in fields.items()})
                    row.update(source_close=row['close'],amount=None,currency='CNY',counting_convention='provider_native_unverified',
                        price_status='traded' if row['volume'] is not None and row['volume']>0 else 'no_trade_reference')
                    if row['volume']==0:
                        for k in ('open','high','low','close'):row[k]=None
                        issues['zero_no_trade_price_as_missing']=FailureKind.NONE
                    elif row['volume'] is None:row['price_status']='unknown'
                    if _selected(request)&{'volume','open_interest'}:issues['fiona_counting_convention_unverified']=FailureKind.UPSTREAM_SCHEMA
                    if 'amount' in _selected(request):issues['fiona_amount_unit_unconfirmed_withheld']=FailureKind.UPSTREAM_SCHEMA
                    if option and code in {'90008034','RB2610C3000'} and _selected(request)&{'volume','amount','open_interest'}:issues['known_source_conflict']=FailureKind.UPSTREAM_SCHEMA
                records.append(row)
        if set(native.values())-{r['symbol'] for r in records}:complete=False;issues['requested_symbols_without_rows']=FailureKind.UPSTREAM_SCHEMA
        records.sort(key=lambda r:(r['symbol'],r.get('bar_time',r['trade_date'])))
        selected=_selected(request)
        return DataPage([{k:v for k,v in r.items() if k in selected} for r in records],
            Provenance('fiona','Fiona MCP',datetime.now(timezone.utc),authority=SourceAuthority.DATA_VENDOR,adapter_mode=AdapterMode.LIVE),
            complete=complete,diagnostics=[Diagnostic(code,'query_data',self.name,failure_kind=failure,adapter_mode=AdapterMode.LIVE) for code,failure in sorted(issues.items())])
