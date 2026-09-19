"""Offline SEC protocol, disclosure semantics, citation and routing regressions."""
from dataclasses import replace
from datetime import date, datetime, timezone
import json

import pytest

from ir_search import MaterialRequest, MaterialSearchRequest, RequestContext, retrieve, search_materials
from ir_search.adapters.sec_materials import SECMaterialAdapter
from ir_search.infrastructure import sec
from ir_search.infrastructure.credentials import SourceConfigError, source_configuration_status
from ir_search.infrastructure.public_web import _Reply
from ir_search.material_registry import MaterialRegistry, build_material_registry
from ir_search.registry import DataAdapterError

NOW = datetime(2026, 9, 17, tzinfo=timezone.utc)
PROFILE = sec.SECProfile('test-client test@example.org')
URL = 'https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm'
HTML = b'''<html><head><title>Annual report 10-K</title></head><body>
<ix:header><ix:hidden><ix:nonnumeric name="dei:DocumentType">10-K</ix:nonnumeric>HIDDEN_XBRL</ix:hidden></ix:header>
<div style="display: none"><p>HIDDEN_STYLE</p><br/></div><div hidden>HIDDEN_ATTRIBUTE</div>
<ix:nonnumeric name="dei:EntityRegistrantName">Apple Inc.</ix:nonnumeric>
<ix:nonnumeric name="dei:DocumentPeriodEndDate">2025-09-27</ix:nonnumeric>
<p>Visible revenue increased; this is issuer disclosure, not regulator endorsement.</p>
<script>HIDDEN_SCRIPT</script><p>Visible risk factors after the hidden content.</p>
<a href="exhibit99.htm">Exhibit 99.1</a><a href="https://evil.example/exhibit.htm">External</a>
<a href="?token=secret">Unsafe</a><link rel="canonical" href="https://evil.example/"/></body></html>'''


def rows(forms=('10-K',), dates=None):
    return {'accessionNumber': [f'0000320193-25-{79+i:06d}' for i in range(len(forms))],
        'filingDate': dates or ['2025-10-31'] * len(forms), 'form': list(forms),
        'primaryDocument': ['aapl-20250927.htm'] * len(forms),
        'reportDate': ['2025-09-27'] * len(forms)}


def submissions(data=None, files=()):
    return {'cik': '320193', 'name': 'Apple Inc.', 'tickers': ['AAPL'],
            'filings': {'recent': data or rows(), 'files': list(files)}}


def setup(monkeypatch, *, data=None, html=HTML, hook=None):
    calls = []
    def transport(url, **kwargs):
        calls.append((url, kwargs))
        kwargs['context'].begin_operation()
        if hook:
            response = hook(url)
            if response is not None: return response
        if url.endswith('company_tickers.json'):
            content = {'0': {'ticker': 'AAPL', 'cik_str': 320193}}
        elif url.endswith('.json'):
            content = data or submissions()
        else:
            return _Reply(200, 'text/html', '', html, NOW)
        return _Reply(200, 'application/json', '', json.dumps(content).encode(), NOW)
    monkeypatch.setattr(sec, '_request', transport)
    monkeypatch.setattr(sec, '_throttle', lambda ctx: ctx.check_active())
    monkeypatch.setattr(sec, 'sec_profile', lambda **kw: PROFILE)
    return calls


def request(**kwargs):
    values = dict(question='10-K', keywords=('10-K',), symbols=('AAPL',), providers=('sec',),
        published_start='2025-10-01', published_end='2025-12-31', sec_forms=('10-K',))
    values.update(kwargs)
    return MaterialSearchRequest(**values)


def registry():
    reg = MaterialRegistry()
    reg.register(SECMaterialAdapter(PROFILE))
    return reg


def test_config_enablement_and_safe_diagnostics(tmp_path):
    assert sec.sec_profile(values={}) is None
    values = {'SEC_MATERIALS_ENABLED': 'true', 'SEC_USER_AGENT': PROFILE.user_agent}
    assert sec.sec_profile(values=values) == PROFILE
    assert PROFILE.user_agent not in repr(PROFILE)
    path = tmp_path / 'sec.env'
    path.write_text('\n'.join(k+'='+v for k, v in values.items()))
    path.chmod(0o600)
    result = source_configuration_status(env_file=path)
    assert PROFILE.user_agent not in json.dumps(result)
    assert next(v for v in result['sources'] if v['provider'] == 'sec')['configured']
    assert [a.name for a in build_material_registry(env_file=path).entries()] == ['sec']


@pytest.mark.parametrize('values', [ {'SEC_MATERIALS_ENABLED': 'yes'},
    {'SEC_MATERIALS_ENABLED': 'true'}, {'SEC_MATERIALS_ENABLED': 'true', 'SEC_USER_AGENT': 'name'},
    {'SEC_MATERIALS_ENABLED': 'true', 'SEC_USER_AGENT': 'test a@b.com\r\nX:1'},
    {'SEC_MATERIALS_ENABLED': 'true', 'SEC_USER_AGENT': PROFILE.user_agent, 'SEC_MAX_HISTORY_FILES': '3'}])
def test_invalid_config(values):
    with pytest.raises(SourceConfigError): sec.sec_profile(values=values)


@pytest.mark.parametrize('kwargs', [{'sec_ciks': ('0',)}, {'sec_ciks': ('../1',)},
    {'sec_ciks': tuple(str(i) for i in range(1, 7))}, {'sec_forms': ('10-k',)}, {'sec_forms': ('10-K?token=x',)}])
def test_contract_rejects_invalid_scope(kwargs):
    with pytest.raises(ValueError): request(**kwargs)


def test_contract_normalizes_cik_and_supports_exact_amendments():
    assert request(sec_ciks=('320193', '0000320193'), sec_forms=('10-K/A',)).sec_ciks == ('0000320193',)


def test_search_metadata_dates_exhibits_and_citations(monkeypatch):
    calls = setup(monkeypatch)
    result = search_materials(request(), registry=registry())
    assert result.items and not result.complete
    version = result.items[0]['versions'][0]
    assert version['source_ref'] == URL and version['published_on'] == '2025-10-31'
    assert version['published_at'] is None and version['period_start'] is None
    assert version['read_details']['filing']['report_date'] == '2025-09-27'
    assert version['provenance']['authority'] == 'official_filing'
    assert version['provenance']['evidence_type'] == 'financial_report'
    assert version['text_scope'] == 'extracted_text' and 'HIDDEN_' not in version['text']
    assert len(version['links']) == 1 and len(calls) == 3
    assert 'sec_exhibits_not_automatically_read' in version['warnings']
    assert all(s['text'] == version['text'][s['start_char']:s['end_char']]
               for s in version['evidence_spans'] if s['source_part'] == 'text')


def test_retrieve_original_provenance_hash_and_offsets(monkeypatch):
    calls = setup(monkeypatch)
    result = retrieve(MaterialRequest(question='revenue', urls=(URL,)))
    assert len(result.materials) == 1
    item = result.materials[0]
    assert item.original_url == URL and item.provenance.authority.value == 'official_filing'
    assert item.provenance.evidence_type.value == 'financial_report'
    assert item.read_details['filing']['filing_date'] is None
    assert item.read_details['filing']['report_date'] == '2025-09-27'
    assert item.read_details['filing']['company'] == 'Apple Inc.'
    assert len(item.text_hash) == 64 and item.evidence_spans
    assert all(s['text'] == item.text[s['start_char']:s['end_char']] for s in item.evidence_spans)
    assert len(calls) == 1 and 'HIDDEN_' not in item.text


def test_bounded_metadata_only_and_amendments_not_merged(monkeypatch):
    setup(monkeypatch, data=submissions(rows(('10-K', '10-K/A'))))
    result = search_materials(request(sec_forms=('10-K', '10-K/A'), text_reads_per_source=0), registry=registry())
    assert len(result.items) == 2
    assert all(g['versions'][0]['text_scope'] == 'metadata' for g in result.items)
    assert {g['versions'][0]['read_details']['filing']['is_amendment'] for g in result.items} == {True, False}
    result = search_materials(request(sec_forms=('10-K', '10-K/A'), text_reads_per_source=2), registry=registry())
    assert len(result.items) == 2  # Identical body text cannot erase distinct accession IDs.


def test_cik_skips_ticker_directory_and_candidate_budget(monkeypatch):
    calls = setup(monkeypatch, data=submissions(rows(('10-K', '10-K/A'))))
    result = search_materials(request(symbols=(), sec_ciks=('320193',), sec_forms=('10-K', '10-K/A'),
        text_reads_per_source=0, candidates_per_source=1), registry=registry())
    assert len(calls) == 1 and result.coverage[0]['scanned_count'] == 1
    assert result.coverage[0]['scans'][0]['has_more']
    assert 'sec_candidate_limit_reached' in [d.code for d in result.diagnostics]


def test_scope_required_and_dry_run_no_network(monkeypatch):
    calls = setup(monkeypatch)
    assert search_materials(request(symbols=()), registry=registry()).coverage[0]['state'] == 'sec_company_scope_required'
    assert search_materials(request(dry_run=True), registry=registry()).plan['source_calls_started'] == 0
    assert not calls


def test_text_failure_retains_only_metadata_and_failure(monkeypatch):
    def hook(url):
        if '/Archives/' in url:
            exc = DataAdapterError('entitlement_denied'); exc.http_status = 403; raise exc
    setup(monkeypatch, hook=hook)
    result = search_materials(request(), registry=registry())
    assert result.items[0]['versions'][0]['text_scope'] == 'metadata'
    assert 'sec_access_blocked' in [d.code for d in result.diagnostics]
    assert not retrieve(MaterialRequest(question='revenue', urls=(URL,))).materials


@pytest.mark.parametrize('url', [URL.replace('https:', 'http:'), URL+'?token=x', URL+'#item1',
    URL.replace('www.sec.gov', 'www.sec.gov.evil.example'), URL.replace('aapl-20250927.htm', '../a.htm'),
    URL.replace('aapl-20250927.htm', '0000320193-25-000079-index.html')])
def test_unsafe_or_index_urls_never_fetched(monkeypatch, url):
    calls = setup(monkeypatch)
    with pytest.raises(DataAdapterError): sec.read_sec_document(url, context=RequestContext(), profile=PROFILE)
    assert not calls


def test_no_redirect_header_leak_and_429(monkeypatch):
    setup(monkeypatch, hook=lambda url: _Reply(302, '', 'https://evil.example/', b'', NOW))
    with pytest.raises(DataAdapterError, match='web_redirect_limit'):
        sec.read_sec_document(URL, context=RequestContext(), profile=PROFILE)
    def limited(url, **kwargs): raise DataAdapterError('rate_limit')
    monkeypatch.setattr(sec, '_request', limited)
    with pytest.raises(DataAdapterError, match='rate_limit'):
        sec.read_sec_document(URL, context=RequestContext(), profile=PROFILE)


def test_history_window_and_cache(monkeypatch):
    history = {'name': 'CIK0000320193-submissions-001.json', 'filingFrom': '2020-01-01', 'filingTo': '2024-12-31'}
    def hook(url):
        if '-submissions-' in url:
            historical = rows(dates=['2024-10-31'])
            historical['accessionNumber'] = ['0000320193-24-000079']
            return _Reply(200, 'application/json', '', json.dumps(historical).encode(), NOW)
    calls = setup(monkeypatch, data=submissions(files=(history,)), hook=hook)
    context = RequestContext()
    values, _, limited = sec._filings('0000320193', PROFILE, context, date(2024, 1, 1), date(2024, 12, 31))
    assert len(values) == 2 and not limited
    sec._filings('0000320193', PROFILE, context, date(2024, 1, 1), date(2024, 12, 31))
    assert len(calls) == 2


def test_history_limit_malformed_rows_and_unresolved_symbol(monkeypatch):
    history = {'name': 'CIK0000320193-submissions-001.json', 'filingFrom': '2020-01-01', 'filingTo': '2024-12-31'}
    setup(monkeypatch, data=submissions(files=(history,)))
    _, _, limited = sec._filings('0000320193', replace(PROFILE, max_history_files=0), RequestContext(), date(2024,1,1), date(2024,12,31))
    assert limited
    with pytest.raises(DataAdapterError, match='upstream_schema'):
        sec._rows({'accessionNumber': []}, '0000320193', 'Apple', ('AAPL',))
    with pytest.raises(DataAdapterError, match='sec_symbol_unresolved'):
        sec._resolve_symbols(('UNKNOWN',), PROFILE, RequestContext())


def test_truncation_and_mcp_payload(monkeypatch):
    setup(monkeypatch)
    from ir_search.mcp_server import search_materials_payload
    result = search_materials_payload(request(sec_ciks=('320193',), max_chars=50).to_dict(), registry=registry())
    version = result['items'][0]['versions'][0]
    assert len(version['text']) <= 50 and 'text_truncated' in version['warnings']
    json.dumps(result)


def test_pdf_warning_contract_and_empty_document(monkeypatch):
    from ir_search.documents import pdf
    from ir_search.documents.models import Document
    from ir_search.models import SourceTier, EvidenceType
    url = URL.rsplit('/', 1)[0] + '/exhibit.pdf'
    setup(monkeypatch, hook=lambda u: _Reply(200, 'application/pdf', '', b'%PDF-synthetic', NOW))
    doc = Document('doc_test', url, url, 'Exhibit', 'web', SourceTier.MEDIA, EvidenceType.ANNOUNCEMENT,
        'pdf', None, NOW, 'pymupdf', 'revenue ' * 30, warnings=['text truncated to max_chars=200'])
    monkeypatch.setattr(pdf, 'extract_pdf_document', lambda *a, **kw: replace(doc))
    result = sec.read_sec_document(url, context=RequestContext(), profile=PROFILE)
    assert result.source == 'sec' and 'text_truncated' in result.warnings
    assert all(' ' not in w for w in result.warnings)
    doc.text = ''
    with pytest.raises(DataAdapterError, match='no_extracted_text'):
        sec.read_sec_document(url, context=RequestContext(), profile=PROFILE)


def test_cancelled_request_never_sends_contact(monkeypatch):
    from ir_search.context import RequestStopped
    calls = setup(monkeypatch)
    context = RequestContext(); context.cancel()
    with pytest.raises(RequestStopped, match='cancelled'):
        sec.read_sec_document(URL, context=context, profile=PROFILE)
    assert not calls
