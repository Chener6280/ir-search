from datetime import datetime,timezone
from dataclasses import replace
import json
import pytest
from ir_search import MaterialRegistry,MaterialSearchRequest,search_materials,RequestContext,MaterialRequest,retrieve
from ir_search.adapters.official_materials import HKEXMaterialAdapter,CompanyIRMaterialAdapter
from ir_search.infrastructure.official_materials import _official_identity
from ir_search.infrastructure.public_web import _Reply
from ir_search.documents.html import extract_html_document
from ir_search.infrastructure.web_documents import _annotate
from ir_search.models import SourceTier
from ir_search.registry import DataAdapterError

NOW=datetime(2026,9,18,tzinfo=timezone.utc)
URL='https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0917/a.pdf'
ROW=dict(FILE_LINK=URL,STOCK_CODE='00700<br/>80700',STOCK_NAME='TENCENT',TITLE='Tencent results',LONG_TEXT='Results',DATE_TIME='17/09/2026 17:29',NEWS_ID='1')


def request(provider,**change):
    return MaterialSearchRequest(**dict(dict(question='Tencent results',symbols=['00700.HK'],providers=[provider],published_start='2026-09-01',published_end='2026-09-18',text_reads_per_source=0),**change))


def hk_transport(row=None,**meta):
    def send(url,**kwargs):
        data=[dict(i=7609,c='00700',n='Tencent')] if 'activestock' in url else dict(result=json.dumps([row or ROW]),hasNextRow=False,recordCnt=1,loadedRecord=1,**meta)
        return _Reply(200,'application/json','',json.dumps(data).encode(),NOW)
    return send


def run(adapter,req=None):
    reg=MaterialRegistry();reg.register(adapter);return search_materials(req or request(adapter.name),registry=reg)


def test_exchange_release_time_metadata_identity_and_url():
    r=run(HKEXMaterialAdapter(transport=hk_transport()))
    assert r.items and r.items[0]['versions'][0]['published_at'].endswith('+08:00')
    item=r.items[0];assert item['versions'][0]['text_scope']=='metadata'
    assert item['versions'][0]['provenance']['source_tier']=='EXCHANGE_FILING'


@pytest.mark.parametrize('change',[dict(STOCK_CODE='00999'),dict(FILE_LINK='https://evil.example/f.pdf'),dict(DATE_TIME='17/08/2026 17:29'),dict(NEWS_ID='bad')])
def test_index_mismatch_rejected(change):
    r=run(HKEXMaterialAdapter(transport=hk_transport(dict(ROW,**change))));assert not r.items


def test_challenge_not_empty_success():
    def send(*a,**k):return _Reply(200,'text/html','',b'<html>verify</html>',NOW)
    r=run(HKEXMaterialAdapter(transport=send));assert not r.items and any(d.code=='upstream_schema' for d in r.diagnostics)


def test_unknown_listing_does_not_search_marketwide():
    calls=[]
    def send(url,**kwargs):calls.append(url);return _Reply(200,'application/json','',b'[]',NOW)
    r=run(HKEXMaterialAdapter(transport=send));assert not r.items and len(calls)==1


def test_hk_reader_links_and_citations_keep_tier():
    def reader(url,**kwargs):
        return _annotate(extract_html_document(b'<html><title>Tencent results</title><article><p>Tencent results revenue increased substantially.</p></article></html>',url),'http')
    r=run(HKEXMaterialAdapter(transport=hk_transport(),reader=reader),request('hkex',text_reads_per_source=1))
    assert r.items and r.items[0]['versions'][0]['text_scope']=='extracted_text'


def test_company_ir_reads_only_reviewed_domains_not_unrelated_pdf():
    calls=[]
    def reader(url,**kwargs):
        calls.append(url)
        body='<article><p>Tencent results.</p><a href="https://static.www.tencent.com/a.pdf">2026 results PDF</a><a href="https://evil.example/other.pdf">Bad PDF</a></article>' if not url.endswith('.pdf') else '<article><p>Tencent revenue results.</p></article>'
        return _annotate(extract_html_document(body.encode(),url),'http')
    r=run(CompanyIRMaterialAdapter(reader=reader),request('company_ir',text_reads_per_source=1))
    assert len(r.items)==1 and not any('evil.example' in url for url in calls)
    item=r.items[0]['versions'][0];assert item['published_on'] is None and item['provenance']['source_tier']=='COMPANY'
    assert item['text_scope']=='extracted_text'


def test_ir_unknown_issuer_zero_network_and_preview_zero_network():
    def fail(*a,**k):pytest.fail('unexpected network')
    r=run(CompanyIRMaterialAdapter(reader=fail),request('company_ir',symbols=['01234.HK']));assert not r.items
    r=run(HKEXMaterialAdapter(transport=fail),request('hkex',dry_run=True));assert not r.items


def test_official_path_exact_host_and_retrieve_classification(monkeypatch):
    assert _official_identity(URL)[0]=='hkex'
    assert _official_identity('https://www.hkexnews.hk/search/titlesearch.xhtml') is None
    assert _official_identity('https://tencent.com.evil.example/a.pdf') is None
    assert _official_identity('https://cloud.tencent.com/developer/article/1') is None
    from ir_search.services import retrieval
    doc=_annotate(extract_html_document(b'<article><p>Tencent revenue results with detailed figures in the official disclosure.</p></article>',URL),'http')
    monkeypatch.setattr(retrieval,'read_web_document',lambda *a,**k:doc)
    r=retrieve(MaterialRequest(urls=[URL],question='Tencent revenue'))
    assert r.materials[0].provenance.source_tier==SourceTier.EXCHANGE_FILING
    assert r.materials[0].provenance.authority.value=='official_filing'


def test_ir_release_dateline_filters_outside_window_and_not_report_year():
    def reader(url,**kw):
        body='<p>Directory</p><a href="https://www.tencent.com/a.pdf">2026 Report</a>' if not url.endswith('.pdf') else '<article>For Immediate Release. Hong Kong, 12 August 2026 – Tencent results revenue.</article>'
        return _annotate(extract_html_document(body.encode(),url),'http')
    r=run(CompanyIRMaterialAdapter(reader=reader),request('company_ir',text_reads_per_source=1))
    assert not r.items and any(d.code=='candidate_outside_publication_window' for d in r.diagnostics)
    r=run(CompanyIRMaterialAdapter(reader=reader),request('company_ir',text_reads_per_source=1,published_start='2026-08-01'))
    assert r.items[0]['versions'][0]['published_on']=='2026-08-12'


def test_non_hk_scope_not_spending_source_dispatch_budget():
    def fail(*a,**kw):pytest.fail('non-HK symbol must not dispatch')
    r=run(HKEXMaterialAdapter(transport=fail),request('hkex',symbols=['600519.SH']))
    assert r.coverage[0]['state']=='source_symbol_format_not_supported'
