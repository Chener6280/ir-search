"""RSS fixtures are synthetic, separate from bounded live acceptance."""
from datetime import datetime, timezone
import json

import pytest

from ir_search import MaterialRegistry, MaterialSearchRequest, RequestContext, search_materials
from ir_search.adapters.rss_materials import RSSMaterialAdapter
from ir_search.infrastructure import rss, public_web
from ir_search.infrastructure.credentials import SourceConfigError, source_configuration_status
from ir_search.infrastructure.web_documents import _annotate
from ir_search.documents.html import extract_html_document
from ir_search.material_registry import build_material_registry
from ir_search.registry import DataAdapterError

FEED = 'https://example.com/feed.xml'
NOW = datetime(2026,9,18,tzinfo=timezone.utc)


def xml(*items):
    return ('<rss version="2.0"><channel>'+''.join(items)+'</channel></rss>').encode()


def item(link='/news/1', date='Fri, 18 Sep 2026 01:00:00 +0800', title='Revenue news'):
    return f'<item><title>{title}</title><link>{link}</link><description>&lt;p&gt;Revenue 12; feed excerpt only&lt;/p&gt;</description><pubDate>{date}</pubDate></item>'


def request(**kwargs):
    return MaterialSearchRequest('Revenue', published_start='2026-09-17', published_end='2026-09-19', providers=('rss',), **kwargs)


def adapter(raw, **kwargs):
    return RSSMaterialAdapter(rss.RSSProfile((FEED,)), client=lambda *a, **k:rss.parse_feed(raw, FEED, fetched_at=NOW), **kwargs)


def run(adapter, **kwargs):
    registry = MaterialRegistry()
    registry.register(adapter)
    return search_materials(request(**kwargs), registry=registry)


def test_profile_explicit_limits_and_safe_urls():
    assert rss.rss_profile(values={}) is None
    values = {'RSS_ENABLED':'true','RSS_FEED_URLS':json.dumps([FEED])}
    assert rss.rss_profile(values=values).feed_urls == (FEED,)
    for value in ('{}','[]','["https://127.0.0.1/x"]','["http://example.com/x"]','[1]','broken',json.dumps([FEED]*2)):
        with pytest.raises(SourceConfigError):
            rss.rss_profile(values={**values,'RSS_FEED_URLS':value})
    with pytest.raises(SourceConfigError): rss.rss_profile(values={'RSS_ENABLED':'yes'})


def test_rss_dates_summary_and_relative_link():
    page = rss.parse_feed(xml(item()), FEED, fetched_at=NOW)
    record = page.items[0]
    assert record.url == 'https://example.com/news/1'
    assert record.published_at.isoformat() == '2026-09-18T01:00:00+08:00'
    assert record.excerpt == 'Revenue 12; feed excerpt only'


def test_atom_namespace_base_and_updated_not_published():
    raw = b'''<feed xmlns="http://www.w3.org/2005/Atom" xml:base="https://example.com/blog/">
    <entry xml:base="posts/"><title>Revenue &amp; costs</title><link rel="self" href="bad"/>
    <link href="a"/><updated>2026-09-18T00:00:00Z</updated><summary>Revenue &lt; costs</summary></entry></feed>'''
    record = rss.parse_feed(raw, FEED, fetched_at=NOW).items[0]
    assert record.url == 'https://example.com/blog/posts/a'
    assert record.published_at is None and 'publication_date_unknown' in record.warnings
    assert record.excerpt == 'Revenue < costs'


@pytest.mark.parametrize('encoding', ['utf-8', 'utf-16', 'utf-32'])
def test_xml_entities_rejected_for_all_encodings(encoding):
    raw = '<!DOCTYPE rss [<!ENTITY secret "INJECTED">]><rss version="2.0"><channel>&secret;</channel></rss>'.encode(encoding)
    with pytest.raises(DataAdapterError, match='upstream_schema'):
        rss.parse_feed(raw, FEED, fetched_at=NOW)


def test_feed_limits_html_and_invalid_rows():
    p = rss.parse_feed(xml(item('https://127.0.0.1/private'), item(date='invalid'), *[item('/news/'+str(i)) for i in range(205)]), FEED, fetched_at=NOW)
    assert len(p.items) <= 200 and 'rss_entry_limit' in p.warnings and 'invalid_rss_entry' in p.warnings
    assert p.items[0].published_at is None
    with pytest.raises(DataAdapterError, match='web_content_unsupported'):
        rss.parse_feed(b'<html><p>Login</p></html>', FEED, fetched_at=NOW)
    with pytest.raises(DataAdapterError, match='response_too_large'):
        rss.parse_feed(b'x'*(2*1024*1024+1), FEED, fetched_at=NOW)
    with pytest.raises(DataAdapterError, match='upstream_schema'):
        rss.parse_feed(b'<rss version="2.0">'+b'<x>'*70+b'</x>'*70+b'</rss>', FEED, fetched_at=NOW)


def test_feed_redirect_transport_and_https_downgrade(monkeypatch):
    calls = []
    def send(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            return public_web._Reply(302,'','/actual',b'',NOW)
        return public_web._Reply(200,'text/xml','',xml(item()),NOW)
    monkeypatch.setattr(rss, '_request', send)
    assert rss.fetch_feed(FEED, context=RequestContext()).url == 'https://example.com/actual'
    assert len(calls) == 2
    monkeypatch.setattr(rss, '_request', lambda *a, **k: public_web._Reply(302,'','http://example.com/clear',b'',NOW))
    with pytest.raises(DataAdapterError, match='blocked_url'):
        rss.fetch_feed(FEED, context=RequestContext())


def test_search_dedupe_excerpt_and_no_original_read():
    result = run(adapter(xml(item(), item())), text_reads_per_source=0)
    assert len(result.items) == 1
    version = result.items[0]['versions'][0]
    assert version['text_scope'] == 'source_excerpt'
    assert version['provenance']['authority'] == 'discovery'
    assert version['text_provider'] == 'rss'
    assert version['source_ref'].startswith(FEED+'#entry-')
    assert version['read_details']['feed_excerpt_not_article_body']
    assert any(v.code == 'rss_duplicate_url' for v in result.diagnostics)
    assert any(v['code'] == 'source_scan_incomplete' for v in result.gaps)
    for span in version['evidence_spans']:
        assert span['text'] == version[span['source_part']][span['start_char']:span['end_char']]


def test_date_and_keyword_filter_avoid_read_and_keep_unknown_date():
    calls = []
    def reader(url, **kwargs):
        calls.append(url)
        raise DataAdapterError('entitlement_denied')
    result = run(adapter(xml(item('/old','Fri, 01 Jan 2021 00:00:00 +0000'), item('/unknown','bad'),
                             item('/other',title='Unrelated')), reader=reader), keywords=('Revenue',))
    # The third entry's excerpt also matches Revenue: test an explicit no-hit below.
    assert '/old' not in ''.join(calls)
    assert any('publication_date_unknown' in v['warnings'] for g in result.items for v in g['versions'])
    nohit = run(adapter(xml(item()), reader=reader), keywords=('profit_only',))
    assert nohit.items == []


def test_original_text_success_and_challenge_retained_as_excerpt():
    def reader(url, **kwargs):
        html = '<title>Original Revenue</title><p>Revenue original body 234.</p>'
        return _annotate(extract_html_document(html.encode(), url), 'http')
    result = run(adapter(xml(item()), reader=reader))
    v = result.items[0]['versions'][0]
    assert v['text_scope'] == 'extracted_text' and v['text_provider'] == 'web'
    assert v['read_details']['publication_basis'] == 'feed_metadata'
    def challenge(url, **kwargs):
        return _annotate(extract_html_document(b'<title>Just a moment</title><p>Verify you are human</p>', url), 'http')
    v = run(adapter(xml(item()), reader=challenge)).items[0]['versions'][0]
    assert v['text_scope'] == 'source_excerpt' and 'Verify you are human' not in v['text']


def test_registry_health_and_zero_call_preview(tmp_path):
    path=tmp_path/'rss.env'
    path.write_text('RSS_ENABLED=true\nRSS_FEED_URLS=\''+json.dumps([FEED])+'\'\n')
    path.chmod(0o600)
    registry=build_material_registry(env_file=path)
    assert [a.name for a in registry.entries()] == ['rss']
    result=search_materials(request(dry_run=True, web_read_mode='firecrawl'),registry=registry)
    assert result.plan['source_calls_started'] == 0
    assert result.plan['source_plans'][0]['max_cloud_scrapes'] == 3
    state=next(s for s in source_configuration_status(env_file=path)['sources'] if s['provider']=='rss')
    assert state['feed_count'] == 1 and not state['live_verified']
    from ir_search.services.source_diagnostics import diagnose_sources
    report=diagnose_sources(['rss'],env_file=path)
    assert report['sources'][0]['state'] == 'configured_unverified'
    assert report['sources'][0]['reader_configuration']['firecrawl']['state'] == 'disabled'
    json.dumps(result.to_dict())


def test_multiple_feeds_budget_and_failed_feed():
    feeds=(FEED,'https://example.org/feed')
    def client(url, **kwargs):
        if url==FEED: raise DataAdapterError('network')
        return rss.parse_feed(xml(*[item('/'+str(i)) for i in range(5)]),url,fetched_at=NOW)
    result=run(RSSMaterialAdapter(rss.RSSProfile(feeds),client=client),candidates_per_source=3,text_reads_per_source=0)
    assert len(result.items)==3
    assert result.coverage[0]['scanned_count']==3
    assert any(d.code=='network' for d in result.diagnostics)


def test_article_date_overrides_feed_and_out_of_window_filtered():
    def reader(url,**kwargs):
        raw=b'<meta property="article:published_time" content="2025-01-01T00:00:00Z"><title>Revenue</title><p>Revenue 123.</p>'
        return _annotate(extract_html_document(raw,url),'http')
    result=run(adapter(xml(item()),reader=reader))
    assert result.items==[]
    assert any(d.code=='rss_article_outside_publication_window' for d in result.diagnostics)


def test_read_budget_and_redirected_original_duplicates_remain_valid():
    calls=[]
    def reader(url,**kwargs):
        calls.append(url)
        return _annotate(extract_html_document(b'<title>Revenue</title><p>Revenue 123.</p>','https://example.com/canonical'),'http')
    result=run(adapter(xml(item('/a'),item('/b'),item('/c')),reader=reader),text_reads_per_source=2)
    assert len(calls)==2
    assert not any(d.code=='invalid_material_response' for d in result.diagnostics)
    assert sum(len(g['versions']) for g in result.items)==3
