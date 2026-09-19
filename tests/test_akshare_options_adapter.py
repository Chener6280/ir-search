from datetime import datetime, timezone
from dataclasses import replace
from decimal import Decimal
import pytest
from ir_search import DataRequest,DataRegistry,get_data
from ir_search.adapters.akshare_options import AKShareOptionsAdapter

NOW=datetime(2026,9,15,4,tzinfo=timezone.utc)


def run(rows=None, **changes):
    source=rows if rows is not None else [{'日期':'2026-09-15T00:00:00.000','时间':'10:00:00','价格':'0.2','均价':'0.21','成交':3,'持仓':10}]
    calls=[]
    def fetch(function,kwargs,**kw):calls.append((function,kwargs));return source
    registry=DataRegistry();registry.register(AKShareOptionsAdapter(fetch=fetch,now=lambda:NOW))
    req=DataRequest('options_intraday',symbols=['10012416.SH'],start='2026-09-15',end='2026-09-15',market='CN_OPTIONS')
    return get_data(replace(req,**changes),registry=registry),calls


def test_real_shaped_point_prices_have_timezone_and_never_invent_ohlc():
    r,calls=run()
    assert r.status.value=='partial' and r.records[0]['price']==Decimal('.2')
    assert r.records[0]['bar_time'].isoformat()=='2026-09-15T10:00:00+08:00'
    assert not {'open','high','low','close'} & set(r.records[0])
    assert calls==[('option_sse_minute_sina',{'symbol':'10012416'})]


@pytest.mark.parametrize('changes',[{'start':'2026-09-14'},{'symbols':('HO2611-P-3250.CFE',)},{'frequency':'5m'},{'fields':('close',)}])
def test_unavailable_history_contracts_or_ohlc_fail_before_network(changes):
    r,calls=run(**changes);assert not r.records and not calls


def test_stale_duplicate_negative_and_future_observations_are_rejected():
    row={'日期':'2026-09-15','时间':'10:00:00','价格':1,'均价':1,'成交':2,'持仓':3}
    for rows in ([dict(row,日期='2026-09-14')],[row,row],[dict(row,价格=-1)],[dict(row,时间='13:00:00')]):
        r,_=run(rows);assert r.status.value=='error' and not r.records


def test_zero_prices_partial_refusal_and_field_projection():
    r,_=run([{'日期':'2026-09-15','时间':'10:00:00','价格':0,'均价':0,'成交':0,'持仓':0}])
    assert r.records[0]['price'] is None and r.records[0]['source_volume']==0
    assert not run(allow_partial=False)[0].records
    assert set(run(fields=('price',))[0].records[0])=={'symbol','bar_time','trade_date','currency','price'}
