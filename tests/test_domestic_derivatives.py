from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal as D
import re

import pytest

from ir_search import DataRequest, DataRegistry, get_data
from ir_search.adapters.domestic_derivatives import DomesticDerivativesAdapter, _WIND_PRICE
from ir_search.infrastructure.credentials import MySQLProfile


def request(dataset='futures_daily',symbol='IF2609.CFE',**kw):
    return DataRequest(dataset,symbols=[symbol],start='2026-09-11',end='2026-09-11',
        market='CN_OPTIONS' if dataset.startswith('options') else 'CN_FUTURES',**kw)


class Database:
    def __init__(self,provider='wind_mysql',symbol='IF2609.CFE'):
        self.profile=MySQLProfile(provider,'host','db','user','secret',derivatives_amount_multiplier=D(10000))
        self.calls=[]
        values={'open':100,'high':110,'low':90,'close':105,'settlement':112,'previous_settlement':99,
                'volume':10,'amount':1,'open_interest':20}
        self.quote={'S_INFO_WINDCODE':symbol,'TRADE_DT':'20260911',**{v:D(values[k]) for k,v in _WIND_PRICE.items()}}
        self.contract={'ContractInnerCode':42,'ContractCode':'IF2609','ExchangeCode':20,'ContractType':4,'IfReal':1,
             'CMValue':300,'ContractMultiplier':'每点300元','PriceUnit':'指数点','CurrencyCode':1420,
             'EffectiveDate':date(2026,1,19),'LastTradingDate':date(2026,9,18)}
        self.rows={'Fut_ContractMain':[self.contract]}

    def select(self,profile,sql,params,**kw):
        self.calls.append((sql,params))
        table=re.search(r'FROM (\w+)',sql)[1]
        if table in self.rows:return [dict(r) for r in self.rows[table]][:params[-1]]
        if table=='Fut_TradingQuote':
            return [{'ContractInnerCode':42,'TradingDay':datetime(2026,9,11),'OpenPrice':D(100),'HighPrice':D(110),
                'LowPrice':D(90),'ClosePrice':D(105),'SettlePrice':D(112),'PrevSettlePrice':D(99),'TurnoverVolume':D(10),
                'TurnoverValue':D(10000),'OpenInterest':D(20)}]
        return [dict(self.quote)]

    def run(self,req=None):
        registry=DataRegistry();registry.register(DomesticDerivativesAdapter(self.profile,select=self.select))
        return get_data(req or request(),registry=registry)


@pytest.mark.parametrize('provider',['wind_mysql','jydb'])
def test_futures_native_prices_separate_settlement_and_contract_count(provider):
    db=Database(provider);r=db.run()
    assert r.status.value=='ok'
    row=r.records[0]
    assert row['settlement']>row['high'] and row['volume']==10 and row['amount']==10000
    assert row['source_close']==105 and row['price_status']=='traded'
    assert all(' JOIN ' not in sql and sql.endswith('LIMIT %s') for sql,_ in db.calls)


def test_no_trade_zero_is_not_reported_as_zero_trade_and_source_close_is_retained():
    db=Database();db.quote.update(S_DQ_VOLUME=D(0),S_DQ_OPEN=D(0),S_DQ_HIGH=D(0),S_DQ_LOW=D(0),S_DQ_CLOSE=D(0))
    r=db.run();row=r.records[0]
    assert r.status.value=='ok' and row['close'] is None and row['source_close']==0
    assert row['price_status']=='no_trade_reference' and row['settlement']==112
    assert 'zero_no_trade_price_as_missing' in [d.code for d in r.diagnostics]


def test_known_open_interest_conflict_is_partial_and_not_masked_by_reconciliation():
    db=Database(symbol='90008034.SZ')
    req=request('options_daily','90008034.SZ')
    r=db.run(req)
    assert r.status.value=='partial' and r.records[0]['open_interest']==20
    assert 'known_source_conflict' in [d.code for d in r.diagnostics]
    assert not db.run(replace(req,allow_partial=False)).records
    r=db.run(replace(req,fields=('close',)))
    assert r.status.value=='ok' and 'known_source_conflict' not in [d.code for d in r.diagnostics]


def test_wind_metadata_gap_routes_whole_contract_request_to_jydb():
    wind=Database();jydb=Database('jydb');registry=DataRegistry(use_source_policy=True)
    for db in (wind,jydb):registry.register(DomesticDerivativesAdapter(db.profile,select=db.select))
    r=get_data(request('futures_contracts'),registry=registry)
    assert r.status.value=='ok' and r.provenance.provider=='jydb'
    assert r.records[0]['contract_multiplier']==300 and not wind.calls
    assert 'fallback_used' in [d.code for d in r.diagnostics]


def test_lifecycle_filter_prevents_cross_decade_first_match_and_maps_native_exchange_code():
    db=Database('jydb');db.contract.update(ContractCode='SR701',ExchangeCode=15,EffectiveDate=date(2026,1,19),LastTradingDate=date(2027,1,14))
    r=db.run(request('futures_contracts','SR701.CZC'))
    assert r.records[0]['exchange']=='CZCE'
    sql,params=db.calls[0]
    assert 'EffectiveDate <= %s' in sql and 'LastTradingDate >= %s' in sql and 15 in params
    db.contract['LastTradingDate']=date(2017,1,14)
    assert db.run(request('futures_contracts','SR701.CZC')).status.value=='error'


def test_options_contract_both_providers_preserve_type_strike_size():
    for provider in ('wind_mysql','jydb'):
        db=Database(provider)
        if provider=='wind_mysql':
            db.rows['chinaoptiondescription']=[dict(OBJECT_ID='o1',S_INFO_WINDCODE='10012416.SH',S_INFO_CODE='10012416',
                S_INFO_SCCODE='588080OP.SH',S_INFO_CALLPUT='708002000',S_INFO_STRIKEPRICE=D('1.4'),S_INFO_COUNIT=10000,
                S_INFO_FTDATE='20260907',S_INFO_LASTTRADINGDATE='20261028')]
        else:
            db.rows['Opt_OptionContract']=[dict(InnerCode=5,ContractCode=10012416,TradingCode='588080P2610M01400',Exchange=83,
                ULAInnerCode=123,ULAType=2,ContractType=3,ContractSize=10000,StrikePrice=D('1.4'),ListingDate=date(2026,9,7),
                LastTradingDate=date(2026,10,28),IfAdjusted=2,IfReal=1)]
        r=db.run(request('options_contracts','10012416.SH'))
        assert r.status.value=='ok' and r.records[0]['option_type']=='put'
        assert r.records[0]['strike']==D('1.4') and r.records[0]['contract_size']==10000


def test_calendar_is_a_real_open_date_dataset_without_fake_currency():
    db=Database();db.rows['cfuturescalendar']=[{'S_INFO_EXCHMARKET':'CFFEX','TRADE_DAYS':'20260911'}]
    r=db.run(request('trading_calendar','CFFEX'))
    assert r.status.value=='ok' and r.records==[{'symbol':'CFFEX','trade_date':date(2026,9,11)}]


@pytest.mark.parametrize('symbol',['IF0.CFE','RB00.SHF','SR999.CZC',"IF2609.CFE' OR 1=1",'IF2609.US'])
def test_bad_or_continuous_contract_codes_do_not_reach_network(symbol):
    db=Database();r=db.run(request(symbol=symbol))
    assert not r.records and not db.calls


def test_independent_derivative_multiplier_and_negative_activity_are_guarded():
    db=Database();db.profile=replace(db.profile,amount_multiplier=D(1000),derivatives_amount_multiplier=None)
    assert not db.run().records and not db.calls
    assert db.run(request(fields=['close'])).records
    db.profile=replace(db.profile,derivatives_amount_multiplier=D(10000));db.quote['S_DQ_VOLUME']=D(-1)
    assert db.run().status.value=='error'


def index_request(symbols=('000300.SH',),**kw):
    return DataRequest('index_daily',symbols=list(symbols),start='2026-09-11',end='2026-09-11',market='CN_INDEX',**kw)


def test_index_levels_are_wind_only_index_points():
    db=Database();db.rows['aindexeodprices']=[{'S_INFO_WINDCODE':'000300.SH','TRADE_DT':'20260911','CRNCY_CODE':'CNY',
        'S_DQ_PRECLOSE':D('4517.2785'),'S_DQ_OPEN':D(0),'S_DQ_HIGH':D(4520),'S_DQ_LOW':D(4430),'S_DQ_CLOSE':D('4439.1443')}]
    r=db.run(index_request())
    assert r.status.value=='ok' and r.records[0]['close']==D('4439.1443') and r.records[0]['open'] is None
    assert r.records[0]['previous_close']==D('4517.2785') and r.records[0]['currency']=='CNY'
    assert {'index_level_not_tradable_price','nonpositive_index_level_as_missing'}<={d.code for d in r.diagnostics}
    assert 'FROM aindexeodprices' in db.calls[0][0]
    db.calls.clear();assert not db.run(index_request(['IF2609.CFE'])).records and not db.calls
    jy=Database('jydb');assert not jy.run(index_request()).records and not jy.calls


def valuation_request(symbols=('000016.SH',),**kw):
    return DataRequest('index_valuation',symbols=list(symbols),start='2026-09-11',end='2026-09-11',market='CN_INDEX',**kw)


def rate_request(symbols=('SHIBOR_3M(10)',),**kw):
    return DataRequest('benchmark_rate',symbols=list(symbols),start='2026-09-11',end='2026-09-11',market='CN_RATE',**kw)


def test_index_dividend_yield_is_vendor_trailing_percent():
    db=Database();db.rows['aindexvaluation']=[{'S_INFO_WINDCODE':'000016.SH','TRADE_DT':'20260911','DIVIDEND_YIELD':D('3.2479')}]
    r=db.run(valuation_request())
    assert r.status.value=='ok' and r.records==[{'symbol':'000016.SH','trade_date':date(2026,9,11),'dividend_yield':D('3.2479')}]
    assert 'FROM aindexvaluation' in db.calls[0][0]
    db.calls.clear();assert not db.run(valuation_request(['IF2609.CFE'])).records and not db.calls
    jy=Database('jydb');assert not jy.run(valuation_request()).records and not jy.calls


def test_benchmark_rate_fixings_keep_vendor_code():
    db=Database();db.rows['windbenchmarkinterestrate']=[{'B_INFO_BENCHMARKCODE':'SHIBOR_3M(10)','TRADE_DT':'20260911','B_INFO_RATE':D('1.4301')}]
    r=db.run(rate_request())
    assert r.status.value=='ok' and r.records==[{'symbol':'SHIBOR_3M(10)','trade_date':date(2026,9,11),'rate':D('1.4301')}]
    assert 'FROM windbenchmarkinterestrate' in db.calls[0][0]
    db.calls.clear();assert not db.run(rate_request(["SHIBOR_3M' OR 1=1--"])).records and not db.calls
    jy=Database('jydb');assert not jy.run(rate_request()).records and not jy.calls


class Chain(Database):
    def __init__(self):
        super().__init__()
        term=dict(S_INFO_SCCODE='IO.CFE',S_INFO_STRIKEPRICE=D(4400),S_INFO_COUNIT=D(100),S_INFO_MONTH='202610',
                  S_INFO_FTDATE='20260824',S_INFO_LASTTRADINGDATE='20261016')
        self.directory=[dict(term,S_INFO_WINDCODE='IO2610-C-4400.CFE',S_INFO_EXCODE='IO2610-C-4400',S_INFO_CALLPUT=D(708001000)),
                        dict(term,S_INFO_WINDCODE='IO2610-P-4400.CFE',S_INFO_EXCODE='IO2610-P-4400',S_INFO_CALLPUT=D(708002000)),
                        dict(term,S_INFO_WINDCODE='IO2610-X-4400.CFE',S_INFO_EXCODE=None,S_INFO_CALLPUT=D(1)),
                        dict(term,S_INFO_WINDCODE='M2611-C-3000.DCE',S_INFO_EXCODE='M2611-C-3000',S_INFO_CALLPUT=D(708001000))]
        quote={'TRADE_DT':'20260911','S_DQ_OPEN':D(30),'S_DQ_HIGH':D(33),'S_DQ_LOW':D(29),'S_DQ_CLOSE':D(31),
               'S_DQ_SETTLE':D('31.2'),'S_DQ_PRESETTLE':D(28),'S_DQ_VOLUME':D(12),'S_DQ_OI':D(40)}
        self.quotes=[dict(quote,S_INFO_WINDCODE='IO2610-C-4400.CFE'),
                     dict(quote,S_INFO_WINDCODE='IO2610-P-4400.CFE',S_DQ_OPEN=D(0),S_DQ_HIGH=D(0),S_DQ_LOW=D(0),S_DQ_CLOSE=D(0),S_DQ_VOLUME=D(0))]

    def select(self,profile,sql,params,**kw):
        self.calls.append((sql,params))
        if 'FROM chinaoptiondescription' in sql:
            rows=[r for r in self.directory if r['S_INFO_WINDCODE'].endswith(params[0][1:])]
            return sorted((dict(r) for r in rows),key=lambda r:r['S_INFO_WINDCODE'])[:params[-1]]
        codes=set(params[:-3])
        return [dict(q) for q in self.quotes if q['S_INFO_WINDCODE'] in codes][:params[-1]]


def test_chain_joins_directory_terms_to_eod_quotes_per_exchange():
    db=Chain();r=db.run(request('options_chain','CFFEX'))
    assert r.status.value=='ok' and [x['contract'] for x in r.records]==['IO2610-C-4400.CFE','IO2610-P-4400.CFE']
    call,put=r.records
    assert call['symbol']=='CFFEX' and call['series_id']=='IO.CFE' and call['option_type']=='call' and call['strike']==4400
    assert call['contract_size']==100 and call['contract_month']=='202610' and call['last_trading_date']==date(2026,10,16)
    assert call['price_status']=='traded' and call['close']==31 and call['settlement']==D('31.2')
    assert put['option_type']=='put' and put['close'] is None and put['source_close']==0 and put['price_status']=='no_trade_reference'
    codes={d.code for d in r.diagnostics}
    assert {'chain_contracts_with_unparsed_terms_skipped','eod_quotes_not_synchronized_no_bid_ask','zero_no_trade_price_as_missing'}<=codes
    assert db.calls[0][1][0]=='%.CFE' and 'JOIN' not in db.calls[0][0] and 'FROM chinaoptioneodprices' in db.calls[1][0]


@pytest.mark.parametrize('change',[dict(symbols=['CFE']),dict(start='2026-08-01'),dict(provider='jydb')])
def test_chain_rejects_non_exchange_symbols_long_ranges_and_jydb(change):
    db=Chain();db.profile=replace(db.profile,provider=change.pop('provider','wind_mysql'))
    r=db.run(replace(request('options_chain','CFFEX'),**change))
    assert not r.records and not db.calls


def test_chain_drops_identical_duplicate_source_rows_but_conflicts_still_fail():
    db=Chain();db.quotes.insert(1,dict(db.quotes[0]))  # 供应商整行重复装载：去重、留诊断、不报错
    r=db.run(request('options_chain','CFFEX'))
    assert r.status.value=='ok' and len(r.records)==2
    assert 'duplicate_source_rows_dropped' in {d.code for d in r.diagnostics}
    db=Chain();db.quotes.insert(1,dict(db.quotes[0],S_DQ_CLOSE=D(32)))  # 同键不同值=源数据冲突，仍须报错
    r=db.run(request('options_chain','CFFEX'))
    assert r.status.value=='error' and 'upstream_schema' in {d.code for d in r.diagnostics}
