"""Standalone single-table fund queries extracted from documented Wind fund schemas."""
from functools import partial
import re
from ir_search.contracts import AdapterMode, DataCapability
from ir_search.contracts.expanded_data import FUND_DATASETS
from ir_search.infrastructure.mysql import _select
from ir_search.registry import DataAdapterError
from ._market_common import _Task, _day, _identity, _in, _number, _page, _validate_request

# Actual server spellings differ from OCR dictionaries (e.g. F_NAV_UNIT).
_MAP = {
 'fund_profile': ('chinamutualfunddescription','F_INFO_WINDCODE','F_INFO_SETUPDATE',
  ('F_INFO_NAME','F_INFO_MATURITYDATE','F_INFO_LISTDATE','F_INFO_EXCHMARKET','F_INFO_TYPE','LISTEDFUNDORNOT','F_INFO_BENCHMARK','CRNY_CODE')),
 'fund_nav': ('chinamutualfundnav','F_INFO_WINDCODE','PRICE_DATE',
  ('ANN_DATE','F_NAV_UNIT','F_NAV_ACCUMULATED','F_NAV_ADJUSTED','F_PRT_NETASSET','F_ASSET_MERGEDSHARESORNOT','CRNCY_CODE')),
 'fund_shares': ('chinamutualfundshare','F_INFO_WINDCODE','END_DT',
  ('ANN_DATE','F_UNIT_TOTAL','FUNDSHARE','FUNDSHARE_TOTAL','F_INFO_SHARE','F_UNIT_MERGEDSHARESORNOT','CHANGEREASON')),
 'fund_holdings': ('chinamutualfundstockportfolio','S_INFO_WINDCODE','F_PRT_ENDDATE',
  ('ANN_DATE','S_INFO_STOCKWINDCODE','F_PRT_STKVALUE','F_PRT_STKQUANTITY','F_PRT_STKVALUETONAV','REPORT_TYPE','CRNCY_CODE')),
 'fund_exchange_daily': ('chinaclosedfundeodprice','S_INFO_WINDCODE','TRADE_DT',
  ('S_DQ_OPEN','S_DQ_HIGH','S_DQ_LOW','S_DQ_CLOSE','S_DQ_VOLUME','S_DQ_AMOUNT','DISCOUNT_RATE','CRNCY_CODE')),
}


def _optional(value):
    return None if value in (None,'') else str(value)


def _currency(raw):
    value=raw.get('CRNCY_CODE',raw.get('CRNY_CODE'))
    if value is not None and (not isinstance(value,str) or not re.fullmatch('[A-Z]{3}',value)):
        raise DataAdapterError('upstream_schema')
    return value


class FundAdapter:
    """Wind fund/ETF support; JYDB NAV fallback retains unsupported fields as null."""
    def __init__(self, profile, *, select=None):
        if profile.provider not in {'wind_mysql','jydb'}: raise ValueError('Unsupported fund source')
        self.name,self._profile,self._select=profile.provider,profile,select or _select
        datasets=FUND_DATASETS if self.name=='wind_mysql' else ('fund_nav',)
        self.capabilities=tuple(DataCapability(self.name,d,tuple(f.name for f in FUND_DATASETS[d].fields),('CN_FUND',),
            frequencies=('snapshot' if d=='fund_profile' else 'report' if d=='fund_holdings' else '1d',),adjustments=('none',),
            supports_pagination=True,adapter_mode=AdapterMode.LIVE,
            coverage_notes=('Exact share classes; no A/C or exchange/OTC alias merging.',
                'Date bounds select inception/NAV/effective/report/trading date, not public availability.',
                'Current vendor snapshot; corrected records preserved; no PIT guarantee.',
                'ETF uses listed fund quotes plus separate NAV/shares; not IOPV or primary-market basket.')) for d in datasets)

    def query_data(self,request,*,context):
        """Read bounded source records without joins or synthetic fund-flow calculations."""
        _validate_request(request,context,{c.dataset for c in self.capabilities},'CN_FUND')
        cap=next(c for c in self.capabilities if c.dataset==request.dataset)
        if request.frequency not in cap.frequencies or request.adjustment!='none' or any(not re.fullmatch(r'\d{6}\.(OF|SH|SZ)',s) for s in request.symbols):
            raise DataAdapterError('unsupported')
        if request.dataset=='fund_exchange_daily' and any(s.endswith('.OF') for s in request.symbols):raise DataAdapterError('unsupported')
        issues={'database_snapshot_not_pit','share_classes_not_merged'}
        if self.name=='jydb':return _page(self,request,self._jydb(request,context,issues),context,issues)
        if request.dataset=='fund_holdings':issues.update({'reported_equity_holdings_only','quarterly_holdings_may_be_top_ten'})
        if request.dataset=='fund_shares':issues.add('share_change_is_not_cash_flow')
        if request.dataset=='fund_exchange_daily':issues.add('source_discount_nav_date_unverified')
        table,symbol,day,columns=_MAP[request.dataset]
        task=_Task(table,('OBJECT_ID',symbol,day)+columns,(symbol+' IN '+_in(request.symbols),day+' BETWEEN %s AND %s'),
            request.symbols+(request.start.strftime('%Y%m%d'),request.end.strftime('%Y%m%d')),
            (day,symbol,'OBJECT_ID'),partial(self._row,request=request))
        return _page(self,request,(task,),context,issues)

    def _row(self,r,issues,*,request):
        d=request.dataset;_,symbol,day,_=_MAP[d]
        if r[symbol] not in request.symbols:raise DataAdapterError('upstream_schema')
        row={'symbol':r[symbol],'source_record_id':_identity(r['OBJECT_ID']),FUND_DATASETS[d].date_field:_day(r[day])}
        if d!='fund_shares':row['currency']=_currency(r)
        if d=='fund_profile':
            row.update(name=_identity(r['F_INFO_NAME']),maturity_date=_day(r['F_INFO_MATURITYDATE'],nullable=True),
                listing_date=_day(r['F_INFO_LISTDATE'],nullable=True),exchange=_optional(r['F_INFO_EXCHMARKET']),
                fund_type=_optional(r['F_INFO_TYPE']),listed_flag=_optional(r['LISTEDFUNDORNOT']),benchmark=_optional(r['F_INFO_BENCHMARK']))
        elif d=='fund_nav':
            row.update(unit_nav=_number(r['F_NAV_UNIT']),accumulated_nav=_number(r['F_NAV_ACCUMULATED']),
                adjusted_nav=_number(r['F_NAV_ADJUSTED']),net_assets=_number(r['F_PRT_NETASSET']),merged_share_class_flag=_optional(r['F_ASSET_MERGEDSHARESORNOT']))
        elif d=='fund_shares':
            row.update(share_class_shares=_number(r['FUNDSHARE'])*10000 if r['FUNDSHARE'] is not None else None,
                combined_shares=_number(r['FUNDSHARE_TOTAL'])*10000 if r['FUNDSHARE_TOTAL'] is not None else None,
                total_shares=_number(r['F_UNIT_TOTAL'])*10000 if r['F_UNIT_TOTAL'] is not None else None,
                tradable_shares=_number(r['F_INFO_SHARE'])*10000 if r['F_INFO_SHARE'] is not None else None,
                merged_share_class_flag=_optional(r['F_UNIT_MERGEDSHARESORNOT']),change_reason=_optional(r['CHANGEREASON']))
        elif d=='fund_holdings':
            row.update(holding_symbol=_identity(r['S_INFO_STOCKWINDCODE']),market_value=_number(r['F_PRT_STKVALUE']),quantity=_number(r['F_PRT_STKQUANTITY']),
                nav_weight=_number(r['F_PRT_STKVALUETONAV'])/100 if r['F_PRT_STKVALUETONAV'] is not None else None,source_report_type=_optional(r['REPORT_TYPE']))
        else:
            row.update({k:_number(r['S_DQ_'+k.upper()]) for k in ('open','high','low','close')})
            volume,amount=_number(r['S_DQ_VOLUME']),_number(r['S_DQ_AMOUNT'])
            row.update(source_volume=volume,source_amount=amount,volume=volume*100 if volume is not None else None,
                amount=amount*1000 if amount is not None else None,source_discount_percent=_number(r['DISCOUNT_RATE']))
            for k in ('open','high','low','close'):
                if row[k] is not None and row[k]<=0:row[k]=None;issues.add('zero_or_negative_fund_price_as_missing')
            if row['high'] is not None and row['low'] is not None and row['high']<row['low']:raise DataAdapterError('upstream_schema')
        if d in {'fund_nav','fund_shares','fund_holdings'}:row['announcement_date']=_day(r['ANN_DATE'],nullable=True)
        return row

    def _jydb(self,request,context,issues):
        tasks=[]
        for symbol in request.symbols:
            code,suffix=symbol.split('.')
            condition='SecuMarket IS NULL' if suffix=='OF' else 'SecuMarket = %s'
            args=(code,) if suffix=='OF' else (code,83 if suffix=='SH' else 90)
            rows=self._select(self._profile,'SELECT InnerCode, SecuCode, SecuMarket, SecuCategory FROM SecuMain WHERE SecuCode = %s AND SecuCategory = 8 AND '+condition+' ORDER BY InnerCode LIMIT %s',args+(3,),max_rows=3,context=context)
            if not rows:continue
            if len(rows)!=1 or str(rows[0]['SecuCode'])!=code or rows[0]['SecuCategory']!=8:raise DataAdapterError('upstream_schema')
            inner=rows[0]['InnerCode']
            def normalize(r,issues,symbol=symbol,inner=inner):
                if r['InnerCode']!=inner:raise DataAdapterError('upstream_schema')
                issues.add('fund_currency_and_adjusted_nav_mapping_incomplete')
                return dict(symbol=symbol,source_record_id=_identity(r['ID']),nav_date=_day(r['EndDate']),
                    announcement_date=_day(r['InfoPublDate'],nullable=True),unit_nav=_number(r['UnitNV']),accumulated_nav=_number(r['AccumulatedUnitNV']),
                    adjusted_nav=None,net_assets=_number(r['NV']),merged_share_class_flag=None,currency=None)
            tasks.append(_Task('MF_NetValue',('ID','InnerCode','EndDate','InfoPublDate','UnitNV','AccumulatedUnitNV','NV'),
                ('InnerCode = %s','EndDate BETWEEN %s AND %s'),(inner,request.start,request.end),('EndDate','ID'),normalize))
        return tasks
