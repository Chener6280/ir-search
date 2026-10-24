"""Bounded official macro observations, with native dates/units and no interpolation."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import csv, io, json, re, sys
from urllib.parse import urlencode
from ir_search.contracts import AdapterMode, DataCapability, DataPage, Diagnostic, Provenance
from ir_search.contracts.expanded_data import MACRO_DATASETS
from ir_search.infrastructure.public_web import _request
from ir_search.models import FailureKind, SourceAuthority
from ir_search.registry import DataAdapterError
from ._market_common import _number, _selected


# How the timestamp semantics of a series are declared. They are declared per series, from the
# source's own description, never inferred from values or names; what cannot be verified stays empty.
SEMANTICS_RULES={
    'is_period_average':'判据：该值是否是对同一量更高频观测（如日度）做时间平均的结果。源站说明写明为平均的才是 true，'
        '并须在 average_of 写出被平均的更高频率；源站原话与判据冲突时以源站原话为准；定不了就留空。空表示未声明，不等于 false。',
    'period_end':'期间最后一天（含）。仅当 period_calendar=calendar（源站的期间标签就是日历期间）时，由已声明的频率和期初按日历推出；'
        'unverified 一律为空。',
    'release_lag_days':'声明的约定：期末之后大约多少天可得。它是约定不是源站事实，必须带出处和核对日期；'
        '服务端永不把它折算成任何日期，也不用它满足 as_of。',
}
_FREQUENCIES=('daily','weekly','monthly','quarterly','annual') # from the highest to the lowest
_DECLARED=('is_period_average','average_of','period_calendar','semantics_source','semantics_checked_on',
           'release_lag_days','release_lag_source','release_lag_checked_on','semantics_note')


def _declaration(row):
    """Turn the declared columns of one catalog row into typed values; a closed vocabulary, or ValueError."""
    if any(not isinstance(row.get(name),str) for name in _DECLARED+('frequency',)):raise ValueError('Incomplete macro catalog row')
    if row['is_period_average'] not in {'','true','false'} or row['period_calendar'] not in {'calendar','unverified'}:
        raise ValueError('Unknown macro semantics value')
    average={'':None,'true':True,'false':False}[row['is_period_average']]
    # The rule above, made checkable: an average names the higher frequency it averages, and nothing else names one.
    if average is True:
        if (row['frequency'] not in _FREQUENCIES or row['average_of'] not in _FREQUENCIES
                or _FREQUENCIES.index(row['average_of'])>=_FREQUENCIES.index(row['frequency'])):
            raise ValueError('A period average must name the higher frequency it averages')
    elif row['average_of']:raise ValueError('Only a period average names what it averages')
    lag=None
    if row['release_lag_days']:
        if not re.fullmatch('[0-9]{1,4}',row['release_lag_days']):raise ValueError('Invalid release lag')
        lag=int(row['release_lag_days'])
    for declared,source,checked in ((average is not None or row['period_calendar']=='calendar','semantics_source','semantics_checked_on'),
                                    (lag is not None,'release_lag_source','release_lag_checked_on')):
        if declared:
            if not row[source].strip():raise ValueError('A declaration needs its source')
            date.fromisoformat(row[checked]) # and the day it was checked
        elif row[source] or row[checked]:raise ValueError('A source without a declaration')
    return dict(row,is_period_average=average,release_lag_days=lag)


def _catalog():
    with (Path(__file__).parents[1]/'entities'/'macro_series.csv').open(encoding='utf8',newline='') as f:
        return {(r['source'],r['series_id']):_declaration(r) for r in csv.DictReader(f)}


# ECB daily reference rates are matched by pattern, not listed in the catalog, so their declaration lives here.
_ECB_EXR_DAILY=_declaration(dict(frequency='daily',is_period_average='false',average_of='',period_calendar='calendar',
    semantics_source='https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html',
    semantics_checked_on='2026-09-20',release_lag_days='',release_lag_source='',release_lag_checked_on='',
    semantics_note='当日参考汇率：依据各央行之间每日约 14:10 CET 的协商程序确定，约 16:00 CET 更新，不是日内平均。'
        '序列键末位 A 在 ECB 的编码里表示"平均"，但日频下就是当日这一个参考汇率。'))


def _period_end(day,spec):
    """Calendar arithmetic on the declared frequency, only for a verified calendar and a canonical period start."""
    if spec['period_calendar']!='calendar':return None
    frequency=spec['frequency']
    if frequency=='daily':return day
    months={'monthly':1,'quarterly':3,'annual':12}.get(frequency)
    if months is None or day.day!=1 or (day.month-1)%months:return None
    following=day.month+months
    return date(day.year+(following-1)//12,(following-1)%12+1,1)-timedelta(days=1)


def _spec(symbol):
    parts=symbol.split('.')
    catalog=_catalog()
    if len(parts)==2 and parts[0]=='FRED' and ('FRED',parts[1]) in catalog:
        return dict(catalog['FRED',parts[1]])
    if len(parts)>=4 and parts[0]=='WB' and re.fullmatch('[A-Z]{3}',parts[1]) and ('WB','.'.join(parts[2:])) in catalog:
        return dict(catalog['WB','.'.join(parts[2:])],country=parts[1])
    if re.fullmatch(r'ECB\.EXR\.D\.[A-Z]{3}\.EUR\.SP00\.A',symbol):
        return dict(_ECB_EXR_DAILY,source='ECB',series_id=symbol[4:],unit=parts[3]+'_per_EUR',frequency='daily',seasonal_adjustment='not_applicable',
                    source_url='https://data.ecb.europa.eu/data/datasets/EXR/'+symbol[4:])
    raise DataAdapterError('unsupported')


class GlobalMacroAdapter:
    name='global_macro'
    def __init__(self,*,transport=None):
        self._transport=transport or _request
        self.capabilities=(DataCapability(self.name,'macro_series',tuple(f.name for f in MACRO_DATASETS['macro_series'].fields),('GLOBAL',),
            frequencies=('native',),adjustments=('none',),adapter_mode=AdapterMode.LIVE,
            coverage_notes=('WB.<ISO3>.<indicator>: packaged annual indicators across countries; ECB.EXR.D.<CCY>.EUR.SP00.A: daily FX.',
                'FRED.<id>: packaged public CSV series; availability may vary by network.',
                'Dates select observation-period starts, not releases; current revised values, no PIT or interpolation.',
                'At most five series and 5000 observations per series; limit truncation is explicit, narrow dates to continue.')),)

    def query_data(self,request,*,context):
        """Fetch one bounded official response per series; failed series stay visible."""
        if (request.dataset!='macro_series' or request.market!='GLOBAL' or request.frequency!='native' or request.adjustment!='none'
            or request.as_of or request.cursor or not request.start or not 1<=len(request.symbols)<=5 or context.account_scope!='default'
            or request.value_kind.value!='actual' or request.end.year-request.start.year>100):raise DataAdapterError('unsupported')
        specs=[_spec(s) for s in request.symbols] # Validate all inputs before any requests.
        records,diagnostics=[],[Diagnostic('latest_revised_snapshot_not_pit','query_data',self.name)]
        complete=True
        failures=[]
        successful=0
        coverage=[]
        for symbol,spec in zip(request.symbols,specs):
            context.check_active()
            # Facts about the source's answer for this window, taken here: before the limit cut
            # below and whatever fields were selected. No threshold and no judgement.
            facts=dict(symbol=symbol,provider=self.name,source_series_id=spec['series_id'],frequency=spec['frequency'],
                date_field='observation_date',requested_start=request.start,requested_end=request.end)
            try:
                rows,partial=self._read(symbol,spec,request,context)
                successful+=1
                complete=complete and not partial
                if partial:diagnostics.append(Diagnostic('macro_source_truncated','query_data',self.name,failure_kind=FailureKind.UPSTREAM_SCHEMA))
                if not rows:complete=False;diagnostics.append(Diagnostic('macro_series_no_observations','query_data',self.name,message=symbol))
                records.extend(rows)
                valued=[r['observation_date'] for r in rows if r['value'] is not None] # FRED "." rows are not observations
                # truncated wins over empty: the source said it holds more than was read.
                facts.update(status='truncated' if partial else 'ok' if valued else 'empty',first_date=min(valued,default=None),
                    last_date=max(valued,default=None),n_obs=len(valued),n_missing=len(rows)-len(valued))
            except DataAdapterError as exc:
                failures.append(exc)
                complete=False;diagnostics.append(Diagnostic(exc.code,'query_data',self.name,failure_kind=exc.failure_kind,message=symbol))
                facts.update(status='failed',first_date=None,last_date=None,n_obs=None,n_missing=None,code=exc.code)
            coverage.append(facts)
        if failures and successful==0:raise failures[0]
        records.sort(key=lambda r:(r['symbol'],r['observation_date']))
        if len({(r['symbol'],r['observation_date']) for r in records})!=len(records):raise DataAdapterError('upstream_schema')
        if len(records)>request.limit:complete=False;diagnostics.append(Diagnostic('macro_result_limit_narrow_dates','query_data',self.name))
        selected=_selected(request)
        return DataPage([{k:v for k,v in r.items() if k in selected} for r in records[:request.limit]],
            Provenance(self.name,'Official FRED / World Bank / ECB observations; row source_url identifies publisher',datetime.now(timezone.utc),
                authority=SourceAuthority.PUBLIC_MARKET_DATA,adapter_mode=AdapterMode.LIVE),complete=complete,diagnostics=diagnostics,
            series_coverage=coverage)

    def _read(self,symbol,spec,request,context):
        source,series=spec['source'],spec['series_id'];start,end=request.start,request.end
        if source=='FRED':url='https://fred.stlouisfed.org/graph/fredgraph.csv?'+urlencode(dict(id=series,cosd=start.isoformat(),coed=end.isoformat()))
        elif source=='WB':url='https://api.worldbank.org/v2/country/'+spec['country']+'/indicator/'+series+'?'+urlencode(dict(format='json',date=f'{start.year}:{end.year}',per_page=5000))
        else:url='https://data-api.ecb.europa.eu/service/data/EXR/'+series[4:]+'?'+urlencode(dict(startPeriod=start.isoformat(),endPeriod=end.isoformat(),format='csvdata'))
        headers={'Accept':'application/json,text/csv'}
        if source=='FRED':
            # FRED's CSV edge accepts the standard Python client identifier; a custom UA timed out in live acceptance.
            headers['User-Agent']=f'Python-urllib/{sys.version_info.major}.{sys.version_info.minor}'
        reply=self._transport(url,context=context,max_bytes=4*1024*1024,headers=headers,anonymous=True) # public series, no credential
        if reply.status!=200:raise DataAdapterError('network') # No redirect to a different data origin.
        try:
            text=reply.body.decode('utf-8-sig');rows=[];updated=None;partial=False
            if source=='WB':
                payload=json.loads(text)
                if not isinstance(payload,list) or len(payload)!=2 or not isinstance(payload[0],dict) or not isinstance(payload[1],list):raise ValueError()
                meta,items=payload;partial=int(meta['pages'])>1 or int(meta['total'])>len(items)
                updated=date.fromisoformat(meta['lastupdated']) if meta.get('lastupdated') else None
                raw=[]
                for r in items:
                    if r['indicator']['id']!=series or r['countryiso3code']!=spec['country'] or not re.fullmatch('[0-9]{4}',r['date']):raise ValueError()
                    raw.append((r['date']+'-01-01',r['date'],r['value'],None))
            else:
                reader=csv.DictReader(io.StringIO(text));items=[]
                for r in reader:
                    items.append(r)
                    if len(items)>5000:raise ValueError()
                if source=='FRED':
                    if reader.fieldnames not in (['observation_date',series],['DATE',series]):raise ValueError()
                    raw=[(r[reader.fieldnames[0]],r[reader.fieldnames[0]],r[series],None) for r in items]
                else:
                    if not {'KEY','TIME_PERIOD','OBS_VALUE','UNIT','UNIT_MULT','OBS_STATUS'}<=set(reader.fieldnames or []):raise ValueError()
                    if any(r['KEY']!=series or r['UNIT']!=series.split('.')[2] or r['UNIT_MULT']!='0' for r in items):raise ValueError()
                    raw=[(r['TIME_PERIOD'],r['TIME_PERIOD'],r['OBS_VALUE'],r['OBS_STATUS'] or None) for r in items]
            if len(raw)>5000:raise ValueError()
            for day,period,value,status in raw:
                day=date.fromisoformat(day)
                if not start<=day<=end:continue # Annual/monthly dates denote period START, no artificial daily fill.
                value=None if value in (None,'','.') else _number(value,nullable=False)
                rows.append(dict(symbol=symbol,observation_date=day,period=period,value=value,unit=spec['unit'],frequency=spec['frequency'],
                    seasonal_adjustment=spec['seasonal_adjustment'],source_series_id=series,source_url=spec['source_url'],
                    observation_status=status,source_updated_on=updated,
                    # Declared semantics, passed on as declared. The lag is a convention: it is never added to a date here.
                    period_end=_period_end(day,spec),is_period_average=spec['is_period_average'],release_lag_days=spec['release_lag_days']))
            return rows,partial
        except (ValueError,TypeError,KeyError,UnicodeError):raise DataAdapterError('upstream_schema') from None
