"""Official disclosures and issuer materials share the bounded original-text reader."""
from dataclasses import replace
import re
from urllib.parse import urlsplit
from ir_search.contracts import AdapterMode, Diagnostic, Provenance
from ir_search.contracts.materials import MaterialCandidate,MaterialCapability,MaterialKind,MaterialSearchPage,MaterialSourceScan,TextScope
from ir_search.infrastructure.official_materials import _hkex_filings,_issuer,_official_identity
from ir_search.infrastructure.web_documents import read_web_document,_VALID
from ir_search.infrastructure.public_web import _allowed_domain
from ir_search.models import EvidenceType,SourceAuthority,SourceTier,FailureKind
from ir_search.registry import DataAdapterError


def _diag(provider,code,failure=FailureKind.NONE):
    return Diagnostic(code,'search_materials',provider,failure_kind=failure,adapter_mode=AdapterMode.LIVE)


def _read(candidate,request,context,reader,domains):
    document=reader(candidate.original_url,context=context,max_chars=request.max_chars,mode=request.web_read_mode,allowed_domains=domains)
    if document.errors or not document.text.strip() or document.extra.get('web_read',{}).get('content_state') not in _VALID:
        raise DataAdapterError('no_extracted_text')
    identity=_official_identity(document.url)
    if not identity or identity[0]!=candidate.provenance.provider:raise DataAdapterError('blocked_url')
    if candidate.provenance.provider=='company_ir' and 'for immediate release' in document.text[:1500].lower():
        # Explicit issuer press-release dateline only; never PDF creation time or report year.
        from datetime import datetime
        dates=re.findall(r'Hong Kong,\s*(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})',document.text[:2000],re.I)
        if len(set(dates))==1:
            try:
                day,month,year=dates[0].split()
                months=('january','february','march','april','may','june','july','august','september','october','november','december')
                published=datetime(int(year),months.index(month.lower())+1,int(day)).date()
            except ValueError:published=None
            if published:
                candidate=replace(candidate,published_on=published,warnings=tuple(w for w in candidate.warnings if w!='publication_date_unknown'),
                    read_details={**candidate.read_details,'publication_basis':'explicit_press_release_dateline'})
    return replace(candidate,original_url=document.url,text=document.text,text_scope=TextScope.EXTRACTED_TEXT,
        provenance=replace(candidate.provenance,fetched_at=document.fetched_at),text_provider='web',
        links=tuple(document.extra.get('public_links',[])),read_details={**candidate.read_details,**document.extra.get('web_read',{}),
            'source_text_trust':'untrusted','source_hash':document.extra.get('source_hash')},
        warnings=tuple(dict.fromkeys(candidate.warnings+tuple(w for w in document.warnings if re.fullmatch(r'[a-z][a-z0-9_]{0,63}',w))+(('text_truncated',) if any('truncated' in w for w in document.warnings) else ()))))


def _continuation(request,name):
    from ir_search.infrastructure.material_cursor import _decode
    if any(_decode(token)['provider']==name for token in request.source_cursors):raise DataAdapterError('unsupported')


class HKEXMaterialAdapter:
    name='hkex'
    def __init__(self,*,transport=None,reader=None):
        self._transport,self._reader=transport,reader or read_web_document
        self.capability=MaterialCapability(self.name,'official_filings',(MaterialKind.ANNOUNCEMENT,),requires_symbols=True,max_symbols=5,
            search_basis='official_title_index_bounded_original_reads',supports_publication_filter=True,
            coverage_notes=('HKEX active securities resolved by exact HK code; not delisted issuer coverage.',
                'At most 366 calendar days and 1000 index rows per company; title index, not exhaustive full-text search.',
                'Release time is Asia/Hong_Kong; report period is not inferred from release date.'))

    def search_materials(self,request,*,context):
        """Discover exchange originals, then retrieve text within the caller's budget."""
        _continuation(request,self.name)
        rows,scans,missing=_hkex_filings(request.symbols,request.published_start,request.published_end,
            request.candidates_per_source,context,self._transport)
        page=MaterialSearchPage([],min(len(rows),request.candidates_per_source),complete=False)
        for s in scans:
            inspected=sum(r['symbol']==s['symbol'] for r in rows[:request.candidates_per_source])
            page.scans.append(MaterialSourceScan('hkex_titles',MaterialKind.ANNOUNCEMENT,request.published_start,request.published_end,'queried',
                received_count=min(s['received'],1000),inspected_count=inspected,has_more=s['has_more'] or inspected<s['received'],
                symbols=(s['symbol'],),collection_id=s['symbol'],fetched_at=s['fetched_at'],date_filter_basis='upstream'))
            if s['has_more']:page.diagnostics.append(_diag(self.name,'hkex_index_truncated_narrow_dates'))
        if missing:page.diagnostics.append(_diag(self.name,'hkex_active_security_unresolved',FailureKind.UPSTREAM_SCHEMA))
        seen=set();reads=0
        for row in rows[:request.candidates_per_source]:
            if row['url'] in seen:continue
            seen.add(row['url'])
            c=MaterialCandidate(row['url'],row['publisher']+' — '+row['title'],MaterialKind.ANNOUNCEMENT,'official_filings',
                Provenance(self.name,row['publisher'],row['fetched_at'],authority=SourceAuthority.OFFICIAL_FILING,
                    source_tier=SourceTier.EXCHANGE_FILING,evidence_type=EvidenceType.ANNOUNCEMENT,adapter_mode=AdapterMode.LIVE),
                original_url=row['url'],symbols=tuple(dict.fromkeys(v['symbol'] for v in rows if v['url']==row['url'])),published_on=row['published_at'].date(),published_at=row['published_at'],
                source_document_id=row['document_id'],source_record_type='hkex_title_index',collection_id=row['symbol'],
                read_details={'content_origin':'hkex_title_index','headline_category':row['category'],'index_title':row['title'],'business_period_verified':False},
                warnings=('business_period_not_verified',))
            if reads<request.text_reads_per_source:
                reads+=1
                try:c=_read(c,request,context,self._reader,('hkexnews.hk',))
                except DataAdapterError as exc:
                    page.diagnostics.append(_diag(self.name,exc.code,exc.failure_kind));c=replace(c,warnings=c.warnings+('original_text_unavailable',))
            page.candidates.append(c)
        page.diagnostics.append(_diag(self.name,'bounded_index_and_original_text_search'))
        return page


class CompanyIRMaterialAdapter:
    name='company_ir'
    def __init__(self,*,reader=None):
        self._reader=reader or read_web_document
        self.capability=MaterialCapability(self.name,'company_ir',(MaterialKind.DOCUMENT,),requires_symbols=True,max_symbols=5,
            search_basis='reviewed_issuer_directories_bounded_pdf_links',supports_publication_filter=False,
            coverage_notes=('Packaged issuer catalog: Tencent, Alibaba, Xiaomi; additional issuers require reviewed domains/IR directory URLs.',
                'Official directory links are discovery metadata until original text is read.',
                'No guessed publication dates from report years or URL paths; unknown dates remain explicit.',
                'Bounded directory snapshot, not historical archive or complete site crawl.'))

    def search_materials(self,request,*,context):
        """Use reviewed issuer domains and existing HTTP/browser/Scrapling/Firecrawl readers."""
        _continuation(request,self.name)
        issuers=[(s,_issuer(s)) for s in request.symbols]
        page=MaterialSearchPage([],0,complete=False);seen=set();reads=0
        for symbol,issuer in issuers:
            if issuer is None:page.diagnostics.append(_diag(self.name,'company_ir_issuer_not_cataloged',FailureKind.UNIMPLEMENTED));continue
            for seed in issuer.directory_urls:
                if page.scanned_count>=request.candidates_per_source:break
                try:document=self._reader(seed,context=context,max_chars=request.max_chars,mode=request.web_read_mode,allowed_domains=issuer.domains)
                except DataAdapterError as exc:page.diagnostics.append(_diag(self.name,exc.code,exc.failure_kind));continue
                if document.errors or not _official_identity(document.url):page.diagnostics.append(_diag(self.name,'company_ir_directory_unavailable'));continue
                links=[l for l in document.extra.get('public_links',[]) if l.get('kind')=='pdf'
                    and _allowed_domain(urlsplit(l.get('url','')).hostname or '',issuer.domains) and l.get('url') not in seen]
                chosen=links[:request.candidates_per_source-page.scanned_count]
                page.scans.append(MaterialSourceScan('company_ir_directory',MaterialKind.DOCUMENT,request.published_start,request.published_end,'queried',
                    collection_id=seed,symbols=(symbol,),received_count=len(links),inspected_count=len(chosen),has_more=True,
                    fetched_at=document.fetched_at,date_filter_basis='local_publication_metadata'))
                page.scanned_count+=len(chosen)
                if not links:page.diagnostics.append(_diag(self.name,'company_ir_no_pdf_links'))
                for link in chosen:
                    seen.add(link['url'])
                    c=MaterialCandidate(link['url'],issuer.name+' — '+(link.get('text') or 'IR PDF'),MaterialKind.DOCUMENT,'company_ir',
                        Provenance(self.name,issuer.name,document.fetched_at,authority=SourceAuthority.COMPANY,source_tier=SourceTier.COMPANY,
                            evidence_type=EvidenceType.UNKNOWN,adapter_mode=AdapterMode.LIVE),original_url=link['url'],symbols=(symbol,),
                        collection_id=seed,source_record_type='issuer_directory_link',warnings=('publication_date_unknown','directory_title_not_document_verification'),
                        read_details={'directory_url':seed,'content_origin':'issuer_directory_metadata'})
                    if reads<request.text_reads_per_source:
                        reads+=1
                        try:c=_read(c,request,context,self._reader,issuer.domains)
                        except DataAdapterError as exc:page.diagnostics.append(_diag(self.name,exc.code,exc.failure_kind));c=replace(c,warnings=c.warnings+('original_text_unavailable',))
                    page.candidates.append(c)
        page.diagnostics.append(_diag(self.name,'company_ir_bounded_directory_snapshot'))
        return page
