from datetime import datetime,date,timezone,timedelta
from dataclasses import dataclass,replace
import pytest
from ir_search import DataRegistry,DataRequest,get_data
from ir_search.adapters.fiona import FionaAdapter
from ir_search.infrastructure.fiona import FionaProfile,FionaResponse

PROFILE=FionaProfile('synthetic-private-token')
TODAY=datetime.now(timezone(timedelta(hours=8))).date()


class Client:
    def __init__(self,option=False):
        self.option=option;self.calls=[];self.truncated=False
        self.meta=dict(order_book_id='90008034',exchange='XSHE',listed_date='2026-01-01',de_listed_date='2027-12-31') if option else {'合约代码':'RB2610','交易所代码':'SHFE','上市日期':'2026-01-01','最后交易日':'2027-12-31'}
        self.day='2026-09-11'
        self.quote=dict(order_book_id='90008034',date=self.day+'T00:00:00',open=.05,high=.06,low=.04,close=.05,settlement=.05,prev_settlement=.04,volume=100,open_interest=200,total_turnover=1000) if option else {'合约代码':'RB2610','交易所代码':'SHFE','交易日期':self.day,'开盘价':3000,'最高价':3100,'最低价':2990,'收盘价':3050,'结算价':3060,'前结算价':3000,'成交量':100,'成交额':20,'持仓量':500}
    def read(self,tool,args,*,context):
        self.calls.append((tool,args))
        rows=[self.meta] if tool in {'get_option_contract_info','get_futures_contract_info_table'} else [self.quote]
        return FionaResponse({'structuredContent':dict(items=rows,total=2 if self.truncated and len(self.calls)>1 else 1,returned=1,truncated=self.truncated and len(self.calls)>1)},datetime.now(timezone.utc),'options_market_data' if self.option else 'futures_market_data',tool)


def run(client,**change):
    reg=DataRegistry();reg.register(FionaAdapter(PROFILE,client_factory=lambda *a:client))
    req=DataRequest(**dict(dict(dataset='options_daily' if client.option else 'futures_daily',market='CN_OPTIONS' if client.option else 'CN_FUTURES',symbols=['90008034.SZ'] if client.option else ['RB2610.SHF'],start=client.day,end=client.day),**change))
    return get_data(req,registry=reg)


def test_normalized_prices_but_unverified_amount_withheld():
    c=Client();r=run(c);assert r.status.value=='partial' and r.records[0]['close']==3050 and r.records[0]['amount'] is None
    assert c.calls[0][0]=='get_futures_contract_info_table'
    r=run(Client(),fields=['close']);assert r.status.value=='ok' and 'amount' not in r.records[0]


@pytest.mark.parametrize('change',[dict(exchange='XSHG'),dict(listed_date='2027-01-01'),dict(order_book_id='other')])
def test_option_mapping_must_verify_exchange_code_and_lifecycle(change):
    c=Client(True);c.meta.update(change);r=run(c);assert r.status.value=='error' and len(c.calls)==1


def test_option_conflict_preserved_and_strict_partial_withholds():
    c=Client(True);r=run(c);assert 'known_source_conflict' in [d.code for d in r.diagnostics]
    assert not run(Client(True),allow_partial=False).records


def test_vendor_truncation_never_reports_complete():
    c=Client();c.truncated=True;r=run(c,fields=['close']);assert not r.complete and r.status.value=='partial'


def test_risk_metrics_remain_source_native_and_model_unknown():
    c=Client(True);c.quote=dict(order_book_id='90008034',trading_date=c.day+'T00:00:00',iv=.3,delta=-.2)
    r=run(c,dataset='option_risk');assert r.status.value=='partial' and r.records[0]['model']=='provider_default_undocumented'
    assert r.records[0]['source_gamma'] is None and float(r.records[0]['source_iv'])==.3


def test_bars_exchange_trading_day_preserves_previous_night():
    c=Client();c.day=TODAY.isoformat();prev=(TODAY-timedelta(days=1)).isoformat()
    c.quote=dict(order_book_id='RB2610',trading_date=c.day,datetime=prev+'T21:05:00',open=3000,high=3010,low=2990,close=3005,volume=10,total_turnover=300500,open_interest=100)
    r=run(c,dataset='derivatives_bars');assert r.records[0]['trade_date']==TODAY and r.records[0]['bar_time'].date()==TODAY-timedelta(days=1)
    assert r.records[0]['source_volume']==10 and not r.complete


def test_historical_bars_do_not_silently_extend_user_policy():
    c=Client();c.day='2020-01-01';r=run(c,dataset='derivatives_bars');assert not r.records and not c.calls


def test_unverified_three_digit_czce_alias_no_guess():
    c=Client();r=run(c,symbols=['SR701.CZC']);assert not r.records and not c.calls


def test_unexpected_quotes_and_outside_dates_rejected():
    for change in [{'合约代码':'RB9999'},{'交易日期':'2026-08-01'}]:
        c=Client();c.quote.update(change);assert not run(c).records


def test_live_observed_single_contract_object_shape():
    from ir_search.adapters.fiona import _payload
    response=FionaResponse({'structuredContent':dict(items={'order_book_id':'90008034'},total=None,returned=None,truncated=False)},datetime.now(timezone.utc),'options_market_data','get_option_contract_info')
    assert _payload(response)==([{'order_book_id':'90008034'}],False)
    with pytest.raises(Exception):_payload(replace(response,tool='get_option_history_quotes'))
