"""Domestic contract directories, futures/options EOD and exchange open dates."""
from __future__ import annotations

import re
from datetime import date
from functools import partial

from ir_search.contracts import AdapterMode, DataCapability
from ir_search.contracts.market import MARKET_DATASETS
from ir_search.infrastructure.mysql import _select
from ir_search.registry import DataAdapterError
from ._market_common import _Task, _day, _identity, _in, _number, _page, _selected, _validate_request

_EXCHANGE = {'CFE':'CFFEX','SHF':'SHFE','DCE':'DCE','CZC':'CZCE','INE':'INE','GFE':'GFEX','SH':'SSE','SZ':'SZSE'}
_JY_FUTURE = {'CFE':20,'SHF':10,'DCE':13,'CZC':15,'INE':11,'GFE':17}
_JY_OPTION = dict(_JY_FUTURE, CFE=51, SH=83, SZ=90)
_WIND_PRICE = {'open':'S_DQ_OPEN','high':'S_DQ_HIGH','low':'S_DQ_LOW','close':'S_DQ_CLOSE',
    'settlement':'S_DQ_SETTLE','previous_settlement':'S_DQ_PRESETTLE','volume':'S_DQ_VOLUME','amount':'S_DQ_AMOUNT','open_interest':'S_DQ_OI'}
_JY_PRICE = {'open':'OpenPrice','high':'HighPrice','low':'LowPrice','close':'ClosePrice','settlement':'SettlePrice',
    'previous_settlement':'PrevSettlePrice','volume':'Volume','amount':'Turnover','open_interest':'OpenInterest'}
_WIND_FUT_COLUMNS = ('OBJECT_ID','S_INFO_WINDCODE','S_INFO_CODE','S_INFO_EXCHMARKET','S_INFO_LISTDATE','S_INFO_DELISTDATE','FS_INFO_TYPE')
_JY_FUT_COLUMNS = ('ContractInnerCode','ContractCode','ExchangeCode','ContractType','IfReal','CMValue','ContractMultiplier','PriceUnit','CurrencyCode','EffectiveDate','LastTradingDate')
_WIND_OPT_COLUMNS = ('OBJECT_ID','S_INFO_WINDCODE','S_INFO_CODE','S_INFO_SCCODE','S_INFO_CALLPUT','S_INFO_STRIKEPRICE','S_INFO_COUNIT','S_INFO_FTDATE','S_INFO_LASTTRADINGDATE')
_JY_OPT_COLUMNS = ('InnerCode','ContractCode','TradingCode','Exchange','ULAInnerCode','ULAType','ContractType','ContractSize','StrikePrice','ListingDate','LastTradingDate','IfAdjusted','IfReal')


def _parse_symbol(symbol, option=False):
    parts = symbol.rsplit('.',1)
    if len(parts)!=2 or parts[1] not in (_JY_OPTION if option else _JY_FUTURE):
        raise DataAdapterError('unsupported')
    code, suffix = parts
    if option:
        valid = re.fullmatch(r'\d{8}',code) if suffix in ('SH','SZ') else re.fullmatch(r'[A-Z]{1,3}\d{3,4}-?[CP]-?\d+(?:\.\d+)?',code)
    else:
        valid = re.fullmatch(r'[A-Z]{1,3}\d{1,2}(?:0[1-9]|1[0-2])',code)
    if not valid:
        raise DataAdapterError('unsupported')
    return code,suffix


class DomesticDerivativesAdapter:
    def __init__(self, profile, *, select=None):
        if profile.provider not in {'wind_mysql','jydb'}:
            raise ValueError('Unsupported derivatives provider')
        self.name,self._profile,self._select=profile.provider,profile,select or _select
        caps=[]
        for dataset in ('futures_contracts','options_contracts','futures_daily','options_daily','trading_calendar'):
            if dataset=='trading_calendar' and self.name!='wind_mysql':continue
            fields=tuple(f.name for f in MARKET_DATASETS[dataset].fields)
            notes=['Concrete contracts only; lifecycle-filtered mappings; no continuous-series stitching',
                'Current database snapshot, no point-in-time availability guarantee',
                'Source closing/reference fields and settlement remain separate; source precision can differ']
            if dataset=='futures_contracts' and self.name=='wind_mysql':
                fields=tuple(f for f in fields if f not in ('contract_multiplier','quote_unit','contract_unit'))
                notes.append('Wind contract-property fields not declared; full metadata routes to JYDB')
            if dataset=='options_daily':notes.append('Known SZSE open-interest conflicts return partial data with explicit diagnostics')
            directory=dataset.endswith('_contracts')
            caps.append(DataCapability(self.name,dataset,fields,('CN_OPTIONS' if dataset.startswith('options') else 'CN_FUTURES',),
                frequencies=('snapshot' if directory else '1d',),adjustments=('none' if directory or dataset=='trading_calendar' else 'raw',),
                supports_pagination=True,adapter_mode=AdapterMode.LIVE,coverage_notes=tuple(notes)))
        self.capabilities=tuple(caps)

    def query_data(self, request, *, context):
        """Read fixed single-table tasks and preserve metadata gaps/conflicting source fields."""
        option=request.dataset.startswith('options')
        datasets={cap.dataset for cap in self.capabilities}
        _validate_request(request,context,datasets,'CN_OPTIONS' if option else 'CN_FUTURES')
        directory=request.dataset.endswith('_contracts')
        if request.frequency!=('snapshot' if directory else '1d') or request.adjustment!=('none' if directory or request.dataset=='trading_calendar' else 'raw'):
            raise DataAdapterError('unsupported')
        issues={'database_snapshot_not_pit'}
        if request.dataset=='trading_calendar':
            if any(s not in set(_JY_FUTURE.keys()) | set(_EXCHANGE.values()) for s in request.symbols):raise DataAdapterError('unsupported')
            if any(s not in set(_EXCHANGE[s] for s in _JY_FUTURE) for s in request.symbols):raise DataAdapterError('unsupported')
            tasks=[_Task('cfuturescalendar',('S_INFO_EXCHMARKET','TRADE_DAYS'),
                ('S_INFO_EXCHMARKET IN '+_in(request.symbols),'TRADE_DAYS BETWEEN %s AND %s'),
                tuple(request.symbols)+(request.start.strftime('%Y%m%d'),request.end.strftime('%Y%m%d')),
                ('S_INFO_EXCHMARKET','TRADE_DAYS'),lambda r,i:{'symbol':r['S_INFO_EXCHMARKET'],'trade_date':_day(r['TRADE_DAYS'])})]
            issues.add('calendar_horizon_not_certified')
        else:
            for symbol in request.symbols:_parse_symbol(symbol,option)
            if self.name=='wind_mysql':tasks=self._wind_tasks(request,option,directory,issues)
            else:tasks=self._jydb_tasks(request,option,directory,context,issues)
        return _page(self,request,tasks,context,issues)

    def _wind_tasks(self,request,option,directory,issues):
        if directory:
            table='chinaoptiondescription' if option else 'cfuturesdescription'
            first,last=('S_INFO_FTDATE','S_INFO_LASTTRADINGDATE') if option else ('S_INFO_LISTDATE','S_INFO_DELISTDATE')
            conditions=['S_INFO_WINDCODE IN '+_in(request.symbols),first+' <= %s',last+' >= %s']
            if not option:conditions.append('FS_INFO_TYPE = %s')
            params=tuple(request.symbols)+(request.end.strftime('%Y%m%d'),request.start.strftime('%Y%m%d'))+(() if option else ('1',))
            return [_Task(table,_WIND_OPT_COLUMNS if option else _WIND_FUT_COLUMNS,tuple(conditions),params,('OBJECT_ID',),
                partial(self._wind_contract,request=request,option=option))]
        if self._profile.derivatives_amount_multiplier is None and 'amount' in _selected(request):
            raise DataAdapterError('units_not_configured')
        groups={}
        for symbol in request.symbols:
            code,suffix=_parse_symbol(symbol,option)
            if option:table='chinaoptioneodprices'
            elif suffix!='CFE':table='ccommodityfutureseodprices'
            elif re.match(r'(IF|IH|IC|IM)\d',code):table='cindexfutureseodprices'
            elif re.match(r'(T|TF|TS|TL)\d',code):table='cbondfutureseodprices'
            else:raise DataAdapterError('unsupported')
            groups.setdefault(table,[]).append(symbol)
        return [_Task(table,('S_INFO_WINDCODE','TRADE_DT')+tuple(_WIND_PRICE.values()),
            ('S_INFO_WINDCODE IN '+_in(symbols),'TRADE_DT BETWEEN %s AND %s'),
            tuple(symbols)+(request.start.strftime('%Y%m%d'),request.end.strftime('%Y%m%d')),
            ('S_INFO_WINDCODE','TRADE_DT'),partial(self._wind_quote,request=request)) for table,symbols in sorted(groups.items())]

    def _wind_contract(self,raw,issues,*,request,option):
        symbol=raw['S_INFO_WINDCODE'];code,suffix=_parse_symbol(symbol,option)
        row={'symbol':symbol,'source_contract_id':_identity(raw['OBJECT_ID']),'native_code':raw['S_INFO_CODE'],
             'exchange':_EXCHANGE[suffix],'currency':'CNY',
             'listing_date':_day(raw['S_INFO_FTDATE'] if option else raw['S_INFO_LISTDATE']),
             'last_trading_date':_day(raw['S_INFO_LASTTRADINGDATE'] if option else raw['S_INFO_DELISTDATE'])}
        if option:
            kind={'708001000':'call','708002000':'put'}.get(str(raw['S_INFO_CALLPUT']))
            if kind is None:raise DataAdapterError('upstream_schema')
            row.update(option_type=kind,strike=_number(raw['S_INFO_STRIKEPRICE'],nullable=False),
                contract_size=_number(raw['S_INFO_COUNIT'],nullable=False),underlying_source_id=_identity(raw['S_INFO_SCCODE']),adjustment_status='unverified')
            issues.update({'contract_adjustment_history_not_applied','underlying_identifier_is_provider_native'})
        else:issues.add('wind_contract_property_fields_unavailable')
        self._validate_contract(row,request)
        return row

    def _mapping_query(self,request,option):
        clauses,params=[],[]
        for symbol in request.symbols:
            code,suffix=_parse_symbol(symbol,option)
            column='ContractCode' if not option or suffix in ('SH','SZ') else 'TradingCode'
            exchange='Exchange' if option else 'ExchangeCode'
            clauses.append(f'({column} = %s AND {exchange} = %s)')
            params.extend((int(code) if option and suffix in ('SH','SZ') else code,(_JY_OPTION if option else _JY_FUTURE)[suffix]))
        conditions=['('+' OR '.join(clauses)+')',('ListingDate' if option else 'EffectiveDate')+' <= %s','LastTradingDate >= %s','IfReal = %s']
        params.extend((request.end.isoformat(),request.start.isoformat(),1))
        return tuple(conditions),tuple(params)

    def _jydb_tasks(self,request,option,directory,context,issues):
        conditions,params=self._mapping_query(request,option)
        columns=_JY_OPT_COLUMNS if option else _JY_FUT_COLUMNS
        table='Opt_OptionContract' if option else 'Fut_ContractMain'
        key='InnerCode' if option else 'ContractInnerCode'
        if directory:return [_Task(table,columns,conditions,params,(key,),partial(self._jydb_contract,request=request,option=option))]
        sql='SELECT '+', '.join(columns)+' FROM '+table+' WHERE '+' AND '.join(conditions)+' ORDER BY '+key+' LIMIT %s'
        mapped=self._select(self._profile,sql,params+(201,),max_rows=201,context=context)
        if len(mapped)>200:raise DataAdapterError('upstream_schema')
        identities,groups={},{}
        for raw in mapped:
            row=self._jydb_contract(raw,issues,request=request,option=option)
            identity=raw[key]
            if type(identity) is not int or identity in identities:raise DataAdapterError('upstream_schema')
            identities[identity]=row['symbol']
            table='Opt_DailyQuote' if option else 'Fut_TradingQuote' if raw['ContractType'] in (3,4) else 'Fut_DailyQuote'
            groups.setdefault(table,[]).append(identity)
        if not identities:raise DataAdapterError('not_found')
        if set(identities.values())!=set(request.symbols):issues.add('contract_mapping_incomplete')
        tasks=[]
        for table,ids in sorted(groups.items()):
            identity='ContractInnerCode' if table=='Fut_TradingQuote' else 'InnerCode'
            day={'Fut_TradingQuote':'TradingDay','Fut_DailyQuote':'EndDate','Opt_DailyQuote':'TradingDate'}[table]
            mapping=dict(_JY_PRICE)
            if table=='Fut_TradingQuote':mapping.update(volume='TurnoverVolume',amount='TurnoverValue')
            conditions=[identity+' IN '+_in(ids),day+' BETWEEN %s AND %s']
            params=tuple(ids)+(request.start.isoformat(),request.end.isoformat())
            if table=='Fut_DailyQuote':
                conditions+=['ReportPeriod = %s','ReportArea = %s','AdjustMark = %s'];params+=(5,6403,2)
            tasks.append(_Task(table,(identity,day)+tuple(mapping.values()),tuple(conditions),params,(identity,day),
                partial(self._jydb_quote,request=request,identities=identities,identity=identity,day=day,mapping=mapping)))
        return tasks

    def _jydb_contract(self,raw,issues,*,request,option):
        suffix=next((s for s,v in (_JY_OPTION if option else _JY_FUTURE).items() if v==raw['Exchange' if option else 'ExchangeCode']),None)
        if suffix is None or raw['IfReal']!=1:raise DataAdapterError('upstream_schema')
        native=str(raw['ContractCode']) if not option or suffix in ('SH','SZ') else raw['TradingCode']
        symbol=native+'.'+suffix
        row={'symbol':symbol,'source_contract_id':_identity(raw['InnerCode'] if option else raw['ContractInnerCode']),
            'native_code':raw['TradingCode'] if option else raw['ContractCode'],'exchange':_EXCHANGE[suffix],
            'listing_date':_day(raw['ListingDate'] if option else raw['EffectiveDate']),
            'last_trading_date':_day(raw['LastTradingDate']),'currency':'CNY'}
        if option:
            kind={2:'call',3:'put'}.get(raw['ContractType'])
            if kind is None:raise DataAdapterError('upstream_schema')
            row.update(option_type=kind,strike=_number(raw['StrikePrice'],nullable=False),contract_size=_number(raw['ContractSize'],nullable=False),
                underlying_source_id=_identity(raw['ULAInnerCode']),adjustment_status={1:'adjusted',2:'unadjusted'}.get(raw['IfAdjusted'],'unverified'))
            issues.update({'contract_metadata_is_current_snapshot','underlying_identifier_is_provider_native'})
        else:
            if raw['CurrencyCode']!=1420:
                row['currency']=None;issues.add('contract_currency_unmapped')
            row.update(contract_multiplier=_number(raw['CMValue']),quote_unit=raw['PriceUnit'] or None,contract_unit=raw['ContractMultiplier'] or None)
        self._validate_contract(row,request)
        return row

    @staticmethod
    def _validate_contract(row,request):
        if (row['symbol'] not in request.symbols or row['listing_date']>row['last_trading_date']
                or row['listing_date']>request.end or row['last_trading_date']<request.start
                or (row.get('strike') is not None and row['strike'] < 0)
                or any(row.get(k) is not None and row[k]<=0 for k in ('contract_multiplier','contract_size'))):
            raise DataAdapterError('upstream_schema')

    def _wind_quote(self,raw,issues,*,request):
        return self._quote(raw['S_INFO_WINDCODE'],_day(raw['TRADE_DT']),{k:_number(raw[v]) for k,v in _WIND_PRICE.items()},issues,request)

    def _jydb_quote(self,raw,issues,*,request,identities,identity,day,mapping):
        return self._quote(identities[raw[identity]],_day(raw[day]),{k:_number(raw[v]) for k,v in mapping.items()},issues,request)

    def _quote(self,symbol,day,row,issues,request):
        if symbol not in request.symbols or not request.start<=day<=request.end:raise DataAdapterError('upstream_schema')
        row.update(symbol=symbol,trade_date=day,currency='CNY',source_close=row['close'],counting_convention='source_reported_contract_count',price_status='unknown')
        if day<date(2020,1,1):
            issues.add('historical_counting_convention_not_normalized')
            if symbol.endswith('.DCE') and self.name == 'jydb':row['counting_convention']='source_historical_double_sided_not_normalized'
        elif symbol.endswith('.DCE'):row['counting_convention']='single_sided'
        if row['volume'] is not None:
            row['price_status']='no_trade_reference' if row['volume']==0 else 'traded'
        if row['volume']==0:
            issues.add('no_trade_close_may_be_reference')
            for name in ('open','high','low','close'):
                if row[name]==0:
                    row[name]=None;issues.add('zero_no_trade_price_as_missing')
        if self.name=='wind_mysql' and row['amount'] is not None:
            if self._profile.derivatives_amount_multiplier is not None:row['amount']*=self._profile.derivatives_amount_multiplier
            else:row['amount']=None
            issues.add('derivatives_amount_uses_separate_unit_mapping')
        issues.add('source_amount_precision_may_differ')
        if symbol=='90008034.SZ' and date(2026,9,7)<=day<=date(2026,9,11) and 'open_interest' in _selected(request):
            issues.update({'known_source_conflict','szse_option_open_interest_conflict'})
        return row
