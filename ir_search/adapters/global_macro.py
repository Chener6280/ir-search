"""Bounded official macro observations, with native dates/units and no interpolation."""
from datetime import date, datetime, timezone
from pathlib import Path
import csv, io, json, re, sys
from urllib.parse import urlencode
from ir_search.contracts import AdapterMode, DataCapability, DataPage, Diagnostic, Provenance
from ir_search.contracts.expanded_data import MACRO_DATASETS
from ir_search.infrastructure.public_web import _request
from ir_search.models import FailureKind, SourceAuthority
from ir_search.registry import DataAdapterError
from ._market_common import _number, _selected


def _catalog():
    with (Path(__file__).parents[1]/'entities'/'macro_series.csv').open(encoding='utf8',newline='') as f:
        return {(r['source'],r['series_id']):r for r in csv.DictReader(f)}


def _spec(symbol):
    parts=symbol.split('.')
    catalog=_catalog()
    if len(parts)==2 and parts[0]=='FRED' and ('FRED',parts[1]) in catalog:
        return dict(catalog['FRED',parts[1]])
    if len(parts)>=4 and parts[0]=='WB' and re.fullmatch('[A-Z]{3}',parts[1]) and ('WB','.'.join(parts[2:])) in catalog:
        return dict(catalog['WB','.'.join(parts[2:])],country=parts[1])
    if re.fullmatch(r'ECB\.EXR\.D\.[A-Z]{3}\.EUR\.SP00\.A',symbol):
        return dict(source='ECB',series_id=symbol[4:],unit=parts[3]+'_per_EUR',frequency='daily',seasonal_adjustment='not_applicable',
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
        for symbol,spec in zip(request.symbols,specs):
            context.check_active()
            try:
                rows,partial=self._read(symbol,spec,request,context)
                successful+=1
                complete=complete and not partial
                if partial:diagnostics.append(Diagnostic('macro_source_truncated','query_data',self.name,failure_kind=FailureKind.UPSTREAM_SCHEMA))
                if not rows:complete=False;diagnostics.append(Diagnostic('macro_series_no_observations','query_data',self.name,message=symbol))
                records.extend(rows)
            except DataAdapterError as exc:
                failures.append(exc)
                complete=False;diagnostics.append(Diagnostic(exc.code,'query_data',self.name,failure_kind=exc.failure_kind,message=symbol))
        if failures and successful==0:raise failures[0]
        records.sort(key=lambda r:(r['symbol'],r['observation_date']))
        if len({(r['symbol'],r['observation_date']) for r in records})!=len(records):raise DataAdapterError('upstream_schema')
        if len(records)>request.limit:complete=False;diagnostics.append(Diagnostic('macro_result_limit_narrow_dates','query_data',self.name))
        selected=_selected(request)
        return DataPage([{k:v for k,v in r.items() if k in selected} for r in records[:request.limit]],
            Provenance(self.name,'Official FRED / World Bank / ECB observations; row source_url identifies publisher',datetime.now(timezone.utc),
                authority=SourceAuthority.PUBLIC_MARKET_DATA,adapter_mode=AdapterMode.LIVE),complete=complete,diagnostics=diagnostics)

    def _read(self,symbol,spec,request,context):
        source,series=spec['source'],spec['series_id'];start,end=request.start,request.end
        if source=='FRED':url='https://fred.stlouisfed.org/graph/fredgraph.csv?'+urlencode(dict(id=series,cosd=start.isoformat(),coed=end.isoformat()))
        elif source=='WB':url='https://api.worldbank.org/v2/country/'+spec['country']+'/indicator/'+series+'?'+urlencode(dict(format='json',date=f'{start.year}:{end.year}',per_page=5000))
        else:url='https://data-api.ecb.europa.eu/service/data/EXR/'+series[4:]+'?'+urlencode(dict(startPeriod=start.isoformat(),endPeriod=end.isoformat(),format='csvdata'))
        headers={'Accept':'application/json,text/csv'}
        if source=='FRED':
            # FRED's CSV edge accepts the standard Python client identifier; a custom UA timed out in live acceptance.
            headers['User-Agent']=f'Python-urllib/{sys.version_info.major}.{sys.version_info.minor}'
        reply=self._transport(url,context=context,max_bytes=4*1024*1024,headers=headers)
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
                    observation_status=status,source_updated_on=updated))
            return rows,partial
        except (ValueError,TypeError,KeyError,UnicodeError):raise DataAdapterError('upstream_schema') from None
