"""Explicit probes verify one operation, preserve budgets, and never return source text."""
from datetime import datetime, timezone
import hashlib
import json
from types import SimpleNamespace

import pytest

from ir_search import diagnose_sources, RequestContext
from ir_search.contracts import Diagnostic
from ir_search.context import RequestStopped
from ir_search.documents.html import extract_html_document
from ir_search.infrastructure import web_documents, rss
from ir_search.models import FailureKind
from ir_search.registry import DataAdapterError
from ir_search.services import source_diagnostics as doctor

URL = 'https://example.com/report'
SAMPLE = {'question':'private sample query', 'published_start':'2026-09-01', 'published_end':'2026-09-18'}


def configure(monkeypatch, *providers):
    monkeypatch.setattr(doctor, 'source_configuration_status', lambda **k: {
        'sources':[{'provider':p, 'enabled':True, 'configured':True} for p in providers], 'diagnostics':[]})


def page(**changes):
    d = extract_html_document(b'<title>Report</title><article>Revenue 123.45 million. Private source text.</article>', URL)
    d.extra['web_read'] = {'backend':'http', 'content_state':'article_text', 'attempts':[], **changes}
    return d


def test_web_sample_checks_body_without_claiming_search_or_completeness(monkeypatch):
    configure(monkeypatch, 'web')
    calls = []
    def read(url, **kwargs):
        kwargs['context'].begin_operation()
        calls.append((url, kwargs))
        return page()
    monkeypatch.setattr(web_documents, 'read_web_document', read)
    r = diagnose_sources(['web'], live=True, read_samples={'web':URL})
    s = r['sources'][0]
    assert s['state'] == 'sample_verified' and s['retrieve_live_verified']
    assert s['operation_status']['search_materials']['state'] == 'not_probed'
    assert s['active_backend'] == 'http' and not s['operation_status']['retrieve']['completeness_verified']
    assert calls[0][1]['mode'] == 'http' and calls[0][1]['max_chars'] == 2000
    assert r['source_calls_started'] == r['operations_used'] == 1
    assert 'Private source text' not in json.dumps(r)


@pytest.mark.parametrize('details,code', [
    ({'content_state':'challenge'}, 'web_content_challenge'),
    ({'content_state':'directory_links'}, 'no_extracted_text'),
    ({'attempts':[{'state':'browser_dependency_missing'}]}, 'browser_dependency_missing'),
])
def test_challenge_directory_or_failed_browser_is_not_verified(monkeypatch, details, code):
    configure(monkeypatch, 'web')
    monkeypatch.setattr(web_documents, 'read_web_document', lambda *a, **k:page(**details))
    s = diagnose_sources(['web'], live=True, read_samples={'web':URL})['sources'][0]
    assert not s['retrieve_live_verified'] and s['state'] == 'probe_failed'
    assert s['operation_status']['retrieve']['code'] == code
    assert s['diagnostics'][0]['next_action']


def test_requested_scrapling_fallback_is_not_parser_success(monkeypatch):
    configure(monkeypatch, 'web')
    monkeypatch.setattr(web_documents, 'read_web_document', lambda *a, **k:page(html_extraction={'selected':'stdlib'}))
    s = diagnose_sources(['web'], live=True, read_samples={'web':URL}, web_read_mode='scrapling')['sources'][0]
    assert not s['retrieve_live_verified'] and s['operation_status']['retrieve']['state'] == 'failed'


@pytest.mark.parametrize('providers,kwargs', [
    (['web'], {'read_samples':{'web':URL}}),
    (['rss'], {'live':True, 'read_samples':{'web':URL}}),
    (['web'], {'live':True, 'read_samples':{'web':'https://localhost/a'}}),
    (['web'], {'live':True, 'read_samples':{'web':URL+'?token=private'}}),
    (['web'], {'live':True, 'read_samples':{'web':'https://xueqiu.com/123/456'}}),
    (['web'], {'live':True, 'web_read_mode':'firecrawl'}),
    (['web'], {'live':True, 'search_samples':{'web':{'question':'query'}}}),
    (['web'], {'live':True, 'search_samples':{'web':{**SAMPLE,'providers':['rss']}}}),
    (['web'], {'live':True, 'search_samples':{'web':{**SAMPLE,'text_reads_per_source':10}}}),
    (['web'], {'live':True, 'search_samples':{'web':{'published_start':'2026-09-01','published_end':'2026-09-18'}}}),
    (['fmp'], {'live':True, 'search_samples':{'fmp':SAMPLE}}),
])
def test_invalid_samples_rejected_before_any_source_call(monkeypatch, providers, kwargs):
    monkeypatch.setattr(doctor, 'source_configuration_status', lambda **k:pytest.fail('Validate inputs first'))
    with pytest.raises(ValueError): diagnose_sources(providers, **kwargs)


def search_fixture(monkeypatch, *, diagnostics=(), state='queried', required_inputs=()):
    import ir_search.material_registry as registry_module
    import ir_search.services.material_search as service
    calls = []
    marker = object()
    monkeypatch.setattr(registry_module, 'build_material_registry', lambda **k:calls.append(k) or marker)
    def search(request, *, registry, context):
        assert registry is marker
        assert request.providers == ('web',)
        assert request.candidates_per_source == request.limit == request.max_sources == 1
        assert request.text_reads_per_source == 0 and not request.dry_run
        context.begin_operation()
        calls.append(request)
        return SimpleNamespace(coverage=[{'provider':'web','state':state,'scanned_count':0,'matched_count':0}],
            items=[], required_inputs=list(required_inputs), diagnostics=list(diagnostics), fallback_requests=[], complete=False)
    monkeypatch.setattr(service, 'search_materials', search)
    return calls


def test_empty_successful_query_verifies_execution_not_findings(monkeypatch, tmp_path):
    configure(monkeypatch, 'web')
    calls = search_fixture(monkeypatch)
    env = tmp_path/'private.env'
    r = diagnose_sources(['web'], live=True, search_samples={'web':SAMPLE}, env_file=env)
    s = r['sources'][0]
    assert s['search_live_verified'] and not s['retrieve_live_verified']
    observed = s['operation_status']['search_materials']
    assert observed['returned_groups'] == 0 and observed['scanned_count'] == 0 and not observed['complete']
    assert observed['query_sha256'] == hashlib.sha256(SAMPLE['question'].encode()).hexdigest()
    assert 'private sample query' not in json.dumps(r)
    assert calls[0] == {'env_file':env} and r['operations_used'] == 1


def test_search_failure_stops_same_provider_body_probe_and_redacts(monkeypatch):
    configure(monkeypatch, 'web')
    search_fixture(monkeypatch, diagnostics=[Diagnostic('quota','search_materials','web',failure_kind=FailureKind.QUOTA)])
    monkeypatch.setattr(web_documents, 'read_web_document', lambda *a, **k:pytest.fail('Stop failed source'))
    r = diagnose_sources(['web'], live=True, search_samples={'web':SAMPLE}, read_samples={'web':URL})
    s = r['sources'][0]
    assert s['state'] == 'probe_failed' and not s['search_live_verified']
    assert s['operation_status']['retrieve']['state'] == 'not_probed'
    assert s['diagnostics'][0]['code'] == 'quota'


@pytest.mark.parametrize('state,required', [('source_queries_failed',()), ('queried',('symbols',))])
def test_scope_missing_or_source_not_queried_is_not_verified(monkeypatch, state, required):
    configure(monkeypatch, 'web')
    search_fixture(monkeypatch, state=state, required_inputs=required)
    s = diagnose_sources(['web'], live=True, search_samples={'web':SAMPLE})['sources'][0]
    assert s['state'] == 'probe_failed' and not s['search_live_verified']


def test_rss_live_verifies_feed_snapshots_without_claiming_search_or_article_access(monkeypatch):
    configure(monkeypatch, 'rss')
    monkeypatch.setattr(rss, 'rss_profile', lambda **k:rss.RSSProfile(('https://example.com/a.xml','https://example.com/b.xml')))
    def fetch(url, *, context):
        context.begin_operation()
        return rss.RSSPage(url, (), datetime.now(timezone.utc), 0)
    monkeypatch.setattr(rss, 'fetch_feed', fetch)
    r = diagnose_sources(['rss'], live=True)
    s = r['sources'][0]
    assert s['state'] == 'sample_verified' and r['operations_used'] == 2
    assert s['operation_status']['feed_snapshot']['feed_count'] == 2
    assert not s['search_live_verified'] and not s['retrieve_live_verified']


def test_cancelled_budget_and_unexpected_error_are_safe(monkeypatch):
    configure(monkeypatch, 'web')
    ctx = RequestContext(); ctx.cancel()
    monkeypatch.setattr(web_documents, 'read_web_document', lambda *a, **k:pytest.fail('Cancelled'))
    r = diagnose_sources(['web'], live=True, read_samples={'web':URL}, context=ctx)
    assert r['source_calls_started'] == 0 and r['sources'][0]['diagnostics'][0]['code'] == 'cancelled'
    def fail(*a, **k): raise RuntimeError('private URL token secret')
    monkeypatch.setattr(web_documents, 'read_web_document', fail)
    r = diagnose_sources(['web'], live=True, read_samples={'web':URL})
    assert 'private URL token secret' not in json.dumps(r)
    assert r['sources'][0]['diagnostics'][0]['code'] == 'probe_failed'


def test_mcp_and_cli_samples_forward_to_same_bounded_implementation(monkeypatch, capsys, tmp_path):
    configure(monkeypatch, 'web')
    monkeypatch.setattr(web_documents, 'read_web_document', lambda *a, **k:page())
    from ir_search.mcp_server import source_health_payload
    r = source_health_payload(providers=['web'], live=True, read_samples={'web':URL})
    assert r['operational_status']['sources'][0]['retrieve_live_verified']
    assert source_health_payload(providers=['web'], read_samples={'web':URL})['status'] == 'error'
    assert doctor.main(['--provider','web','--live','--read-url',URL]) == 0
    assert json.loads(capsys.readouterr().out)['sources'][0]['retrieve_live_verified']
    search_fixture(monkeypatch)
    path = tmp_path/'samples.json'; path.write_text(json.dumps({'web':SAMPLE}))
    assert doctor.main(['--provider','web','--live','--search-samples',str(path)]) == 0
    assert json.loads(capsys.readouterr().out)['sources'][0]['search_live_verified']
