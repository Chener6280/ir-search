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
