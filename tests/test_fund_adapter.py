from datetime import date
from decimal import Decimal
from dataclasses import replace
import pytest
from ir_search import DataRequest,DataRegistry,get_data
from ir_search.adapters.funds import FundAdapter
from ir_search.infrastructure.credentials import MySQLProfile

P=MySQLProfile('wind_mysql','host','db','user','test-secret')
NAV=dict(OBJECT_ID='id1',F_INFO_WINDCODE='110011.OF',PRICE_DATE='20260911',ANN_DATE='20260912',F_NAV_UNIT='4',F_NAV_ACCUMULATED='6',F_NAV_ADJUSTED='7',F_PRT_NETASSET=None,F_ASSET_MERGEDSHARESORNOT='0',CRNCY_CODE='CNY')


def run(rows,**changes):
    calls=[]
    def select(p,sql,args,**kwargs):calls.append((sql,args));return rows[:args[-1]]
    registry=DataRegistry();registry.register(FundAdapter(P,select=select))
    req=DataRequest(**dict(dict(dataset='fund_nav',symbols=['110011.OF'],market='CN_FUND',start='2026-09-11',end='2026-09-11'),**changes))
    return get_data(req,registry=registry),calls


def test_nav_exact_share_class_dates_and_noninterchangeable_values():
    r,calls=run([NAV]);row=r.records[0]
    assert r.status.value=='ok' and row['nav_date']==date(2026,9,11) and row['announcement_date']==date(2026,9,12)
    assert (row['unit_nav'],row['accumulated_nav'],row['adjusted_nav'])==(Decimal(4),Decimal(6),Decimal(7))
    assert ' JOIN ' not in calls[0][0] and 'PRICE_DATE BETWEEN %s AND %s' in calls[0][0]
    assert '110011.OF' in calls[0][1]


def test_unit_nav_not_filled_from_other_nav():
    r,_=run([dict(NAV,F_NAV_UNIT=None)]);assert r.records[0]['unit_nav'] is None


def test_shares_wan_conversion_preserves_merge_flag_and_reason():
    r,_=run([dict(OBJECT_ID='s',F_INFO_WINDCODE='510300.SH',END_DT='20260911',ANN_DATE='20260912',F_UNIT_TOTAL='123',FUNDSHARE='80',FUNDSHARE_TOTAL='160',F_INFO_SHARE='100',F_UNIT_MERGEDSHARESORNOT='1',CHANGEREASON='split')],dataset='fund_shares',symbols=['510300.SH'])
    assert r.records[0]['share_class_shares']==800000 and r.records[0]['combined_shares']==1600000
    assert r.records[0]['total_shares']==1230000 and r.records[0]['tradable_shares']==1000000
    assert r.records[0]['merged_share_class_flag']=='1' and 'cash_flow' not in r.records[0]


def test_holdings_weights_and_disclosure_are_preserved_without_full_portfolio_claim():
    r,_=run([dict(OBJECT_ID='h',S_INFO_WINDCODE='110011.OF',F_PRT_ENDDATE='20260911',ANN_DATE='20261020',S_INFO_STOCKWINDCODE='00700.HK',F_PRT_STKVALUE='20',F_PRT_STKQUANTITY='2',F_PRT_STKVALUETONAV='12.3',REPORT_TYPE='quarter',CRNCY_CODE='CNY')],dataset='fund_holdings')
    assert r.records[0]['nav_weight']==Decimal('.123') and 'quarterly_holdings_may_be_top_ten' in [d.code for d in r.diagnostics]


def test_listed_fund_quotes_units_and_zero_prices():
    row=dict(OBJECT_ID='q',S_INFO_WINDCODE='510300.SH',TRADE_DT='20260911',CRNCY_CODE='CNY',S_DQ_OPEN='0',S_DQ_HIGH='4.6',S_DQ_LOW='4.5',S_DQ_CLOSE='4.58',S_DQ_VOLUME='10',S_DQ_AMOUNT='4.58',DISCOUNT_RATE='-.0087')
    r,_=run([row],dataset='fund_exchange_daily',symbols=['510300.SH']);v=r.records[0]
    assert v['open'] is None and v['volume']==1000 and v['amount']==4580 and v['source_discount_percent']==Decimal('-.0087')
    assert 'premium' not in v


def test_profile_current_snapshot_not_asof():
    row=dict(OBJECT_ID='p',F_INFO_WINDCODE='110011.OF',F_INFO_SETUPDATE='20260911',F_INFO_NAME='Fund A',F_INFO_MATURITYDATE=None,F_INFO_LISTDATE=None,F_INFO_EXCHMARKET=None,F_INFO_TYPE='open',LISTEDFUNDORNOT='0',F_INFO_BENCHMARK='benchmark',CRNY_CODE='CNY')
    r,_=run([row],dataset='fund_profile');assert r.records[0]['name']=='Fund A'
    r,calls=run([row],dataset='fund_profile',as_of='2026-09-11T00:00:00Z');assert not r.records and not calls


@pytest.mark.parametrize('change',[{'symbols':['110011']},{'symbols':['110011.SH OR 1=1']},{'adjustment':'forward'},{'frequency':'1m'},{'market':'US'}])
def test_invalid_requests_do_not_query(change):
    r,calls=run([NAV],**change);assert not r.records and not calls


def test_keyset_cursor_bound_to_date_and_share_class():
    r,_=run([NAV,dict(NAV,OBJECT_ID='id2')],limit=1);assert r.next_cursor
    r,calls=run([NAV],limit=1,cursor=r.next_cursor,symbols=['110012.OF'],provider='wind_mysql');assert not r.records and not calls
    assert any(d.code=='invalid_cursor' for d in r.diagnostics)


def test_duplicate_source_identity_not_silently_deduped():
    r,_=run([NAV,NAV]);assert r.status.value=='error' and not r.records


def test_jydb_nav_rejects_ambiguous_code_and_does_not_invent_currency():
    p=replace(P,provider='jydb');calls=[]
    def select(p,sql,args,**kw):
        calls.append(sql)
        if 'FROM SecuMain' in sql:return [dict(InnerCode=7,SecuCode='110011',SecuMarket=None,SecuCategory=8)]
        return [dict(ID=1,InnerCode=7,EndDate=date(2026,9,11),InfoPublDate=date(2026,9,12),UnitNV='4',AccumulatedUnitNV='6',NV='1000')]
    registry=DataRegistry();registry.register(FundAdapter(p,select=select))
    r=get_data(DataRequest('fund_nav',symbols=['110011.OF'],market='CN_FUND',start='2026-09-11',end='2026-09-11'),registry=registry)
    assert r.status.value=='partial' and r.records[0]['currency'] is None and r.records[0]['adjusted_nav'] is None
    assert 'SecuCategory = 8' in calls[0] and 'SecuMarket IS NULL' in calls[0]


def test_next_page_pins_provider_and_passes_signed_keyset():
    first,_=run([NAV,dict(NAV,OBJECT_ID='id2')],limit=1)
    second,calls=run([dict(NAV,OBJECT_ID='id2')],limit=1,cursor=first.next_cursor,provider=first.provenance.provider)
    assert second.status.value=='ok' and second.records[0]['source_record_id']=='id2'
    assert '(PRICE_DATE, F_INFO_WINDCODE, OBJECT_ID) > (%s, %s, %s)' in calls[0][0]
