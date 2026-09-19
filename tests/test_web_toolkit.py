"""Synthetic known answers; cloud fixtures are not live provider acceptance."""
from datetime import datetime, timezone
import hashlib
import json
import sys

import pytest

from ir_search import MaterialRequest, MaterialSearchRequest, RequestContext, retrieve
from ir_search.infrastructure import web_toolkit as toolkit, public_web, web_documents
from ir_search.infrastructure.credentials import SourceConfigError
from ir_search.registry import DataAdapterError

URL = 'https://example.com/report'
HTML = '<title>Annual report</title><article><p>' + 'Revenue 123.45 亿元，未经审计。 ' * 10 + '</p></article>'


def response(**kwargs):
    data = {'rawHtml': HTML, 'metadata': {'url': URL, 'statusCode': 200}}
    data.update(kwargs)
    return public_web._Reply(200, 'application/json', '', json.dumps({'success': True, 'data': data}).encode(), datetime.now(timezone.utc))


@pytest.fixture
def cloud(monkeypatch):
    calls = []
    monkeypatch.setattr(public_web, '_resolve', lambda *a: None)
    def send(url, **kwargs):
        kwargs['context'].begin_operation()
        calls.append((url, kwargs))
        return response()
    monkeypatch.setattr(public_web, '_request', send)
    return calls


def test_firecrawl_opt_in_key_redaction_and_local_status():
    assert toolkit.firecrawl_profile(values={'FIRECRAWL_API_KEY':'private-key'}) is None
    p = toolkit.firecrawl_profile(values={'FIRECRAWL_ENABLED':'true', 'FIRECRAWL_API_KEY':'private-key'})
    assert 'private-key' not in repr(p)
    assert toolkit.web_toolkit_status()['firecrawl']['state'] == 'disabled'
    for key in ('', 'a\nb', '密码'):
        with pytest.raises(SourceConfigError):
            toolkit.firecrawl_profile(values={'FIRECRAWL_ENABLED':'true', 'FIRECRAWL_API_KEY':key})
    with pytest.raises(SourceConfigError):
        toolkit.firecrawl_profile(values={'FIRECRAWL_ENABLED':'yes'})


def test_firecrawl_raw_html_no_llm_no_cookies_no_auto_retry(cloud):
    context = RequestContext()
    d = toolkit.fetch_firecrawl_document(URL, context=context, profile=toolkit.FirecrawlProfile('test-private-key'))
    assert len(cloud) == context.operations == 1
    endpoint, options = cloud[0]
    assert endpoint == 'https://api.firecrawl.dev/v2/scrape'
    payload = json.loads(options['body'])
    assert payload['formats'] == ['rawHtml'] and payload['parsers'] == []
    assert payload['maxAge'] == 0 and payload['storeInCache'] is False
    assert payload['proxy'] == 'basic' and payload['skipTlsVerification'] is False
    assert 'headers' not in payload and 'actions' not in payload
    assert d.url == URL and 'Revenue 123.45' in d.text
    assert 'remote_fetch_redirects_unobserved' in d.warnings
    assert 'test-private-key' not in json.dumps(d.to_dict())


@pytest.mark.parametrize('url', ['http://localhost/a','https://127.0.0.1/a', URL+'?q=private', URL+'#private'])
def test_cloud_rejects_nonpublic_or_query_before_network(cloud, url):
    with pytest.raises(DataAdapterError, match='blocked_url'):
        toolkit.fetch_firecrawl_document(url, context=RequestContext(), profile=toolkit.FirecrawlProfile('test'))
    assert cloud == []


@pytest.mark.parametrize('metadata,code', [
    ({'url':'http://example.com/r','statusCode':200},'blocked_url'),
    ({'url':'https://127.0.0.1/r','statusCode':200},'blocked_url'),
    ({'url':'https://evil.example/r','statusCode':200},'blocked_url'),
    ({'sourceURL':URL,'statusCode':200},'upstream_schema'),
    ({'url':URL},'upstream_schema'),
    ({'url':URL,'statusCode':403},'entitlement_denied'),
    ({'url':URL,'statusCode':429},'rate_limit'),
])
def test_cloud_returned_target_and_status_validation(monkeypatch, cloud, metadata, code):
    monkeypatch.setattr(public_web, '_request', lambda *a, **k: response(metadata=metadata))
    with pytest.raises(DataAdapterError, match=code):
        toolkit.fetch_firecrawl_document(URL, context=RequestContext(), allowed_domains=('example.com',), profile=toolkit.FirecrawlProfile('test'))


@pytest.mark.parametrize('raw', [b'not json', b'{"success": false, "error":"secret"}', b'[]', b'null'])
def test_cloud_errors_are_sanitized(monkeypatch, cloud, raw):
    monkeypatch.setattr(public_web, '_request', lambda *a, **k: public_web._Reply(200,'application/json','',raw,datetime.now(timezone.utc)))
    with pytest.raises(DataAdapterError, match='^upstream_schema$'):
        toolkit.fetch_firecrawl_document(URL, context=RequestContext(), profile=toolkit.FirecrawlProfile('test'))


def test_cloud_disabled_or_cancelled_no_network(cloud):
    with pytest.raises(DataAdapterError, match='source_disabled'):
        toolkit.fetch_firecrawl_document(URL, context=RequestContext())
    context = RequestContext()
    context.cancel()
    from ir_search.context import RequestStopped
    with pytest.raises(RequestStopped, match='cancelled'):
        toolkit.fetch_firecrawl_document(URL, context=context, profile=toolkit.FirecrawlProfile('test'))
    assert not cloud


def test_new_modes_public_contracts_and_retrieve_exact_citations(monkeypatch, cloud):
    monkeypatch.setattr(toolkit, 'firecrawl_profile', lambda: toolkit.FirecrawlProfile('test'))
    for mode in ('scrapling', 'firecrawl'):
        assert MaterialSearchRequest('Revenue', web_read_mode=mode).web_read_mode == mode
    result = retrieve(MaterialRequest('Revenue', [URL], web_read_mode='firecrawl', max_chars=110))
    assert result.materials
    payload = result.to_dict()
    assert 'remote_intermediate_hops_unobserved' in json.dumps(payload)
    # Existing evidence extractor guarantees offsets against the exact returned text.
    d = web_documents.read_web_document(URL, context=RequestContext(), mode='firecrawl', max_chars=110)
    from ir_search.evidence import extract_evidence
    assert d.extra['web_read']['backend'] == 'firecrawl'
    for span in extract_evidence(d, 'Revenue'):
        assert span.text == d.text[span.start_char:span.end_char]


def test_scrapling_missing_before_http(monkeypatch):
    monkeypatch.setitem(sys.modules, 'scrapling', None)
    monkeypatch.setattr(public_web, '_request', lambda *a, **k: pytest.fail('dependency should fail first'))
    with pytest.raises(DataAdapterError, match='dependency_missing'):
        web_documents.read_web_document(URL, context=RequestContext(), mode='scrapling')


@pytest.mark.parametrize('html,selected', [
    ('<nav>Menu</nav>'+HTML+'<footer>Footer</footer>', 'scrapling'),
    ('<title>Report</title><main>'+('Revenue text '*20)+'</main>', 'scrapling'),
    ('<title>Report</title><div>'+('Revenue text '*20)+'</div>', 'stdlib'),
    (HTML+HTML, 'stdlib'),
    ('<title>Just a moment</title>'+HTML, 'stdlib'),
    ('<article>Revenue</article>', 'stdlib'),
])
def test_real_scrapling_semantic_selection_and_fallback(html, selected):
    pytest.importorskip('scrapling')
    d = toolkit.extract_scrapling_document(html.encode(), URL, max_chars=100)
    assert d.extra['html_extraction']['selected'] == selected
    assert d.raw_hash == hashlib.sha1(html.encode()).hexdigest()
    assert d.text_hash == hashlib.sha1(d.text.encode()).hexdigest()
    assert len(d.text) <= 100
    if selected == 'scrapling':
        assert 'Menu' not in d.text and 'Footer' not in d.text
    else:
        assert 'scrapling_selection_fallback' in d.warnings


def test_scrapling_exception_preserves_text(monkeypatch):
    def broken(*a, **k): raise RuntimeError('private parser details')
    monkeypatch.setattr(toolkit, '_scrapling_selector', lambda: broken)
    d = toolkit.extract_scrapling_document(HTML.encode(), URL)
    assert d.extra['html_extraction']['state'] == 'parser_error'
    assert 'Revenue' in d.text and 'private parser details' not in json.dumps(d.to_dict())


def test_cloud_api_redirect_never_forwards_key(monkeypatch, cloud):
    calls=[]
    def redirect(url, **kwargs):
        calls.append(url)
        return public_web._Reply(302,'','https://elsewhere.example/api',b'',datetime.now(timezone.utc))
    monkeypatch.setattr(public_web,'_request',redirect)
    with pytest.raises(DataAdapterError,match='upstream_schema'):
        toolkit.fetch_firecrawl_document(URL,context=RequestContext(),profile=toolkit.FirecrawlProfile('test'))
    assert calls==['https://api.firecrawl.dev/v2/scrape']


def test_cloud_challenge_is_not_retrievable_evidence(monkeypatch, cloud):
    monkeypatch.setattr(toolkit,'firecrawl_profile',lambda:toolkit.FirecrawlProfile('test'))
    monkeypatch.setattr(public_web,'_request',lambda *a,**k:response(rawHtml='<title>Just a moment</title><p>Verify you are human</p>'))
    result=retrieve(MaterialRequest('Revenue',[URL],web_read_mode='firecrawl'))
    assert result.materials==[]
    assert any(v.code=='web_content_challenge' for v in result.diagnostics)


def test_wechat_dedicated_reader_rejects_new_modes_before_network():
    from ir_search.infrastructure.wechat import fetch_wechat_document
    for mode in ('scrapling','firecrawl'):
        with pytest.raises(DataAdapterError,match='web_content_unsupported'):
            fetch_wechat_document('https://mp.weixin.qq.com/s/synthetic',context=RequestContext(),mode=mode)


def test_scrapling_fallback_preserves_base_truncation_warning(monkeypatch):
    class Empty:
        def css(self, selector): return []
    monkeypatch.setattr(toolkit,'_scrapling_selector',lambda:lambda *a,**k:Empty())
    d=toolkit.extract_scrapling_document(b'<div>'+b'Revenue '*14000+b'</div>',URL,max_chars=100000)
    assert len(d.text)<=100000 and 'text_truncated' in d.warnings
