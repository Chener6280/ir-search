"""Public HKEX title search and packaged, reviewed issuer IR directories."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import csv, json, re
from dataclasses import dataclass
from html import unescape
from urllib.parse import urlencode, urljoin, urlsplit
from .public_web import _request, _url, _allowed_domain
from ir_search.registry import DataAdapterError


@dataclass(frozen=True)
class IssuerIR:
    symbol: str
    name: str
    domains: tuple[str,...]
    directory_urls: tuple[str,...]


def _issuers():
    with (Path(__file__).parents[1]/'entities'/'company_ir.csv').open(encoding='utf8',newline='') as f:
        return tuple(IssuerIR(r['symbol'],r['name'],tuple(r['domains'].split('|')),tuple(r['directory_urls'].split('|'))) for r in csv.DictReader(f))


def _hk_code(symbol):
    if not isinstance(symbol,str) or not re.fullmatch(r'\d{4,5}\.HK',symbol):raise DataAdapterError('unsupported')
    return symbol.split('.')[0].zfill(5)


def _issuer(symbol):
    canonical=_hk_code(symbol)+'.HK'
    return next((r for r in _issuers() if r.symbol==canonical),None)


def _official_identity(url):
    try:parsed,host,_=_url(url)
    except DataAdapterError:return None
    if parsed.scheme!='https':return None
    if host in {'www.hkexnews.hk','www1.hkexnews.hk'} and parsed.path.startswith('/listedco/listconews/'):
        return ('hkex','HKEX issuer disclosure','official_filing')
    for issuer in _issuers():
        if _allowed_domain(host,issuer.domains):return ('company_ir',issuer.name,'company')
    return None


def _plain(value):
    if not isinstance(value,str):raise DataAdapterError('upstream_schema')
    return unescape(re.sub('<[^>]+>',' ',value)).strip()


def _json(url,context,transport):
    reply=transport(url,context=context,max_bytes=4*1024*1024,headers={'Accept':'application/json'})
    if reply.status!=200:raise DataAdapterError('network')
    try:return json.loads(reply.body),reply.fetched_at
    except (UnicodeError,ValueError):raise DataAdapterError('upstream_schema') from None


def _hkex_filings(symbols,start,end,limit,context,transport=None):
    transport=transport or _request
    if not start or not end or (end-start).days>366 or not 1<=len(symbols)<=5:raise DataAdapterError('unsupported')
    codes={_hk_code(s):s for s in symbols}
    if len(codes)!=len(symbols):raise DataAdapterError('unsupported')
    active,_=_json('https://www1.hkexnews.hk/ncms/script/eds/activestock_sehk_e.json',context,transport)
    if not isinstance(active,list) or len(active)>50000:raise DataAdapterError('upstream_schema')
    stock_map={}
    for r in active:
        if isinstance(r,dict) and r.get('c') in codes:
            if r['c'] in stock_map or type(r.get('i')) is not int:raise DataAdapterError('upstream_schema')
            stock_map[r['c']]=r
    rows,scans,missing=[],[],[]
    for code,symbol in codes.items():
        if code not in stock_map:missing.append(symbol);continue
        args=dict(sortDir='0',sortByOptions='DateTime',category='0',market='SEHK',stockId=stock_map[code]['i'],documentType='-1',
            fromDate=start.strftime('%Y%m%d'),toDate=end.strftime('%Y%m%d'),title='',searchType='1',t1code='-2',t2Gcode='-2',t2code='-2',rowRange=min(1000,limit),lang='en')
        payload,fetched=_json('https://www1.hkexnews.hk/search/titleSearchServlet.do?'+urlencode(args),context,transport)
        try:
            items=json.loads(payload['result']) if isinstance(payload['result'],str) else payload['result']
            if (not isinstance(items,list) or len(items)>1000 or type(payload['hasNextRow']) is not bool
                    or type(payload['recordCnt']) is not int or payload['recordCnt']<len(items) or int(payload['loadedRecord'])!=len(items)):raise ValueError()
            scans.append(dict(symbol=symbol,total=payload['recordCnt'],received=len(items),has_more=payload['hasNextRow'] or payload['recordCnt']>len(items),fetched_at=fetched))
            for r in items:
                if code not in _plain(r['STOCK_CODE']).split():raise ValueError()
                published=datetime.strptime(r['DATE_TIME'],'%d/%m/%Y %H:%M').replace(tzinfo=timezone(timedelta(hours=8)))
                if not start<=published.date()<=end:raise ValueError()
                url=urljoin('https://www1.hkexnews.hk',r['FILE_LINK'])
                identity=_official_identity(url)
                if not identity or identity[0]!='hkex':raise ValueError()
                if not re.fullmatch('[0-9]+',str(r['NEWS_ID'])):raise ValueError()
                rows.append(dict(symbol=symbol,title=_plain(r['TITLE']),category=_plain(r['LONG_TEXT']),publisher=_plain(r['STOCK_NAME']),
                    url=url,document_id=str(r['NEWS_ID']),published_at=published,fetched_at=fetched))
        except (ValueError,KeyError,TypeError):raise DataAdapterError('upstream_schema') from None
    rows.sort(key=lambda r:(r['published_at'],r['document_id']),reverse=True)
    return rows,scans,missing
