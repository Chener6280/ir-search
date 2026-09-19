"""Offline planning, reviewed resources, stable bounded public reads."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
import json
import socket
from threading import Barrier, Event, Lock
import time

import pytest

from ir_search import (list_institutions, list_material_capabilities, MaterialRegistry, MaterialSearchRequest,
                       RequestContext, search_materials, MaterialRequest, retrieve)
from ir_search.context import RequestStopped
from ir_search.adapters.web_materials import WebMaterialAdapter
from ir_search.documents.html import extract_html_document
from ir_search.infrastructure.credentials import WebMaterialProfile
from ir_search.infrastructure.web_search import WebSearchPage
from ir_search.infrastructure import web_documents, web_browser
from ir_search.institutions import _catalog, _match_institutions, _institution_for_url, _selected_institutions
from ir_search.registry import DataAdapterError
from ir_search import mcp_server


def request(**changes):
    defaults = dict(question='易方达基金市场展望', keywords=('展望',), published_start='2026-09-01',
                    published_end='2026-09-17', providers=('web',), web_institutions=('efunds',))
    return MaterialSearchRequest(**{**defaults, **changes})


class Client:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def search(self, query, *, limit, context):
        context.begin_operation()
        self.calls.append((query, limit))
        return WebSearchPage(self.rows, datetime.now(timezone.utc))


def document(url, text='易方达基金市场展望', extra=''):
    doc = extract_html_document(('<title>市场展望</title><p>' + text + '</p>' + extra).encode(), url)
    doc.extra['adapter_mode'] = 'live'
    return doc


def setup(reader=None, profile=None, rows=None):
    rows = rows or [dict(url='https://www.efunds.com.cn/a', title='易方达基金市场展望', snippet='易方达基金展望')]
    client = Client(rows)
    adapter = WebMaterialAdapter(profile or WebMaterialProfile(allow_anonymous=True), client=client,
                                 bocha_client=client, reader=reader or (lambda url, **kw: document(url)))
    registry = MaterialRegistry()
    registry.register(adapter)
    return registry, client


def test_catalog_is_dated_sourced_packaged_and_fresh_serializable():
    catalog = list_institutions()
    assert catalog['coverage'] == 'curated_seed_not_exhaustive'
    assert len(catalog['institutions']) == 14
    assert {r['category'] for r in catalog['institutions']} == {'fund_manager', 'bank', 'securities', 'regulator'}
    assert all(r['source_url'].startswith('https://') and r['reviewed_on'] == '2026-09-17' for r in catalog['institutions'])
    assert list_material_capabilities(registry=MaterialRegistry())['institution_catalog'] == catalog
    catalog['institutions'][0]['name'] = 'mutated'
    assert list_institutions()['institutions'][0]['name'] != 'mutated'
    assert all(_institution_for_url(row.source_url) == row for row in _catalog())
    json.dumps(catalog)


@pytest.mark.parametrize('text,ids', [('易方达展望', {'efunds'}), ('贝莱德与摩根大通', {'blackrock', 'jpmorgan'}),
    ('CICC outlook', {'cicc'}), ('CICCapital', set()), ('10 sec', set()), ('SEC filing', {'sec'}),
    ('央行 摩根 中信', set()), ('中国证监会期货办法', {'csrc'}), ('美国证监会', {'sec'}),
    ('美国证监会与中国证监会', {'sec', 'csrc'})])
def test_alias_boundaries_and_ambiguous_short_names(text, ids):
    assert {row.institution_id for row in _match_institutions(text)} == ids


@pytest.mark.parametrize('url', ['https://efunds.com.cn.attacker.test/a', 'https://fakeefunds.com.cn',
                               'https://efunds.com.cn@attacker.test', 'file://efunds.com.cn/private'])
def test_lookalike_domains_do_not_gain_official_identity(url):
    assert _institution_for_url(url) is None


@pytest.mark.parametrize('changes', [{'dry_run': 1}, {'web_read_workers': True}, {'web_read_workers': 0},
    {'web_read_workers': 5}, {'web_institutions': ['unknown']}, {'web_institutions': ['pbc'] * 9}])
def test_invalid_new_request_fields(changes):
    with pytest.raises(ValueError): request(**changes)


def test_unknown_catalog_id():
    with pytest.raises(ValueError): _selected_institutions(('unknown',))


def test_material_matching_disambiguates_institutions_in_source_text():
    rows = [dict(url='https://example.test/a', title='美国证监会展望', snippet='美国证监会展望')]
    registry, _ = setup(rows=rows, reader=lambda url, **kw: document(url, text='美国证监会展望'))
    assert not search_materials(request(question='中国证监会展望', web_institutions=()), registry=registry).items
    assert search_materials(request(question='美国证监会展望', web_institutions=()), registry=registry).items
    assert {row.institution_id for row in _match_institutions('美国证监会 ' * 1000)} == {'sec'}


def test_dry_run_no_dns_no_source_calls_and_execution_matches_plan(monkeypatch):
    registry, client = setup()
    context = RequestContext(max_operations=10)
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: pytest.fail('dry run DNS'))
    preview = search_materials(request(dry_run=True, web_read_workers=3), registry=registry, context=context)
    assert not client.calls and context.operations == 0 and not preview.items and not preview.complete
    assert preview.coverage[0]['state'] == 'planned_not_queried'
    assert preview.plan['budget']['cost_estimate'] is None
    assert preview.plan['source_calls_started'] == 0
    assert preview.plan['institutions'][0]['institution_id'] == 'efunds'
    assert preview.plan['selected_providers'] == ['web']
    assert 'dry_run_no_source_calls' in {d.code for d in preview.diagnostics}
    plan = preview.plan['source_plans'][0]
    assert plan['allowed_domains'] == ['efunds.com.cn'] and plan['max_candidates'] == 10
    actual = search_materials(request(web_read_workers=3), registry=registry)
    assert actual.items and client.calls[0] == (plan['routes'][0]['query'], plan['max_candidates'])
    assert actual.items[0]['versions'][0]['provenance']['authority'] == 'company'
    assert actual.plan['selected_providers'] == preview.plan['selected_providers']


def test_preview_blocked_scope_and_missing_inputs_never_query():
    registry, client = setup(profile=WebMaterialProfile(allow_anonymous=True, allowed_domains=('sec.gov',)))
    preview = search_materials(request(dry_run=True), registry=registry)
    assert preview.plan['source_plans'][0]['scope_conflict']
    assert preview.plan['source_plans'][0]['max_discovery_calls'] == 0
    actual = search_materials(request(), registry=registry)
    assert actual.coverage[0]['state'] == 'unsupported' and not client.calls
    result = search_materials(request(dry_run=True, published_start=None, published_end=None), registry=registry)
    assert result.status.value == 'unavailable' and result.required_inputs == ['published_start', 'published_end']
    assert not client.calls


def test_preview_respects_account_exclusions_capabilities_and_source_budget():
    from ir_search import MaterialCapability, MaterialKind
    class Source:
        def __init__(self, name, **kwargs):
            self.name = name
            self.capability = MaterialCapability(name, 'web', (MaterialKind.WEB_PAGE,), **kwargs)
        def search_materials(self, *a, **kw): pytest.fail('preview queried adapter')
    registry = MaterialRegistry()
    for source in (Source('a'), Source('b'), Source('c', requires_symbols=True), Source('d', account_scope='private')):
        registry.register(source)
    result = search_materials(request(dry_run=True, providers=('b','c'), exclude_providers=('a',), max_sources=1), registry=registry)
    assert result.plan['selected_providers'] == ['b']
    states = {row['provider']: row['state'] for row in result.coverage}
    assert 'a' not in states and states['c'] == 'source_requires_symbols' and 'd' not in states


def test_regional_catalog_routing_handles_chinese_foreign_names_without_changing_explicit_scope():
    profile = WebMaterialProfile(search_provider='regional', bocha_api_key='fake_bocha_key', api_key='fake_anysearch_key')
    registry, client = setup(profile=profile)
    result = search_materials(request(question='贝莱德展望', web_institutions=('blackrock',), dry_run=True), registry=registry)
    assert [r['provider'] for r in result.plan['source_plans'][0]['routes']] == ['anysearch']
    result = search_materials(request(question='华夏基金展望', web_institutions=('chinaamc',), dry_run=True), registry=registry)
    assert [r['provider'] for r in result.plan['source_plans'][0]['routes']] == ['bocha']
    result = search_materials(request(web_region='both', candidates_per_source=1, text_reads_per_source=10, dry_run=True), registry=registry)
    plan = result.plan['source_plans'][0]
    assert plan['max_discovery_calls'] == 1 and plan['max_text_reads'] == 1 and not client.calls


def test_parallel_reads_overlap_preserve_order_budget_and_partial_failure():
    gate = Barrier(2)
    calls, lock = [], Lock()
    rows = [dict(url=f'https://www.efunds.com.cn/{i}', title=f'易方达展望{i}', snippet='易方达展望') for i in range(4)]
    def reader(url, **kw):
        kw['context'].begin_operation()
        with lock: calls.append(url)
        gate.wait(timeout=3)
        if url.endswith('/0'):
            raise DataAdapterError('rate_limit')
        return document(url)
    registry, client = setup(reader=reader, rows=rows)
    context = RequestContext(max_operations=4)
    result = search_materials(request(web_read_workers=2, text_reads_per_source=2, web_read_mode='http'), registry=registry, context=context)
    assert len(calls) == 2 and context.operations == 4
    assert len(result.items) == 4
    assert result.coverage[0]['returned_evidence']['text_scope_counts']['extracted_text'] == 1
    assert {'bounded_parallel_web_reads', 'rate_limit'} <= {d.code for d in result.diagnostics}
    # Adapter collection order stays the discovery order, even on a failed read.
    serial, _ = setup(rows=rows)
    page = serial.entries()[0].search_materials(request(text_reads_per_source=0), context=RequestContext())
    assert [c.source_ref for c in page.candidates] == [row['url'] for row in rows]


def test_operation_budget_is_atomic_under_contention():
    context = RequestContext(max_operations=7)
    def operation(_):
        try: context.begin_operation(); return True
        except RequestStopped as exc:
            assert exc.code == 'operation_budget_exhausted'; return False
    with ThreadPoolExecutor(max_workers=16) as pool:
        successes = list(pool.map(operation, range(200)))
    assert sum(successes) == context.operations == 7


def test_host_spacing_and_cancellation_and_request_isolation():
    context = RequestContext()
    assert context._wait_for_public_host('a.test')
    before = time.monotonic()
    assert context._wait_for_public_host('a.test')
    assert time.monotonic() - before >= .20
    context._mark_public_host_limited('a.test')
    assert not context._wait_for_public_host('a.test')
    assert context._wait_for_public_host('b.test')
    assert RequestContext()._wait_for_public_host('a.test')
    context.cancel()
    with pytest.raises(RequestStopped, match='cancelled'): context._wait_for_public_host('b.test')
    deadline = RequestContext(timeout_seconds=.01)
    deadline._wait_for_public_host('a.test')
    with pytest.raises(RequestStopped, match='deadline_exceeded'): deadline._wait_for_public_host('a.test')


def test_cancelled_parallel_read_starts_no_io():
    registry, _ = setup(reader=lambda *a, **kw: pytest.fail('cancelled read'))
    context = RequestContext(); context.cancel()
    result = search_materials(request(web_read_workers=4), registry=registry, context=context)
    assert context.operations == 0 and 'cancelled' in {d.code for d in result.diagnostics}


def test_browser_capable_reads_remain_serial_with_explicit_diagnostic():
    calls = []
    registry, _ = setup(reader=lambda url, **kw: calls.append(url) or document(url))
    preview = search_materials(request(dry_run=True, web_read_workers=4), registry=registry)
    assert preview.plan['source_plans'][0]['read_workers'] == 1
    assert preview.plan['source_plans'][0]['read_workers_requested'] == 4
    result = search_materials(request(web_read_workers=4), registry=registry)
    assert len(calls) == 1 and 'web_parallel_reads_require_http' in {d.code for d in result.diagnostics}


def test_attachment_budget_preserves_original_and_marks_truncation():
    def read(url, **kw):
        doc = document(url)
        doc.extra['public_links'] = [dict(url=f'https://www.efunds.com.cn/{i}.pdf', text=f'附件{i}',
            kind='pdf', status='discovered_not_retrieved') for i in range(70)]
        return doc
    registry, _ = setup(reader=read)
    result = search_materials(request(), registry=registry)
    version = result.items[0]['versions'][0]
    assert len(version['attachments']) == 50 and len(version['links']) == 70
    assert 'attachment_metadata_truncated' in version['warnings']


def test_wechat_phase_context_shares_host_limit_without_losing_its_deadline():
    from ir_search.infrastructure.wechat import _PhaseContext
    parent = RequestContext()
    phase = _PhaseContext(parent, 1)
    assert phase._wait_for_public_host('mp.weixin.qq.com')
    phase._mark_public_host_limited('mp.weixin.qq.com')
    assert not parent._wait_for_public_host('mp.weixin.qq.com')
    phase.deadline = time.monotonic() - 1
    with pytest.raises(RequestStopped, match='deadline_exceeded'):
        phase._wait_for_public_host('different.test')


@pytest.mark.parametrize('institution_id', ['pbc', 'csrc', 'nfra'])
def test_regulator_directory_discovery_keeps_attachments_separate(monkeypatch, institution_id):
    institution = _selected_institutions((institution_id,))[0]
    url = institution.directory_urls[0]
    html = '<title>政策规章目录</title><a href="/policy/1">政策全文</a>'
    html += '<a href="/download?filename=办法.pdf">监督办法</a><a href="/form.docx">申请表</a>'
    html += '<iframe src="/file.pdf" title="图片版"></iframe><a href="/{{x.href}}">{{x.title}}</a>'
    # Rendering removes template placeholders; unrendered ones must stay LOADING.
    monkeypatch.setattr(web_documents, 'fetch_public_document', lambda *a, **kw: document(url, extra=html.replace('{{x.title}}', '')))
    material = retrieve(MaterialRequest('办法', [url], web_read_mode='http')).materials[0]
    assert material.read_details['content_state'] == 'directory_links'
    assert material.provenance.evidence_type.value == 'unknown'
    assert [link['kind'] for link in material.links] == ['pdf', 'pdf', 'attachment', 'web_page']
    assert all(link['status'] == 'discovered_not_retrieved' for link in material.links)
    assert material.read_details['attachment_count'] == 3
    assert material.read_details['institution']['institution_id'] == institution_id


@pytest.mark.parametrize('template', ['{{rulesTitle}}规章 {{x.title}}', '现行有效 {{constTotal}} 部 {{(y+1) + (rulesPageNo-1)*15 }}'])
def test_nfra_template_is_loading_not_policy_evidence(monkeypatch, template):
    url = 'https://www.nfra.gov.cn/cn/view/pages/rulesDetail.html?docId=1027050'
    monkeypatch.setattr(web_documents, 'fetch_public_document', lambda *a, **kw: document(url, text=template))
    result = retrieve(MaterialRequest('规章', [url], web_read_mode='http'))
    assert not result.materials and 'web_content_loading' in {d.code for d in result.diagnostics}
    monkeypatch.setattr(web_browser, 'render_public_document', lambda *a, **kw: document(url, text='金融监管总局监督办法'))
    rendered = web_documents.read_web_document(url, context=RequestContext(), mode='auto')
    assert rendered.extra['web_read']['backend'] == 'crawl4ai'


def test_official_scope_filters_urls_and_redirects_and_preserves_stricter_config():
    profile = WebMaterialProfile(allow_anonymous=True, allowed_domains=('www.efunds.com.cn',))
    rows = [dict(url='https://fake.test/a', title='易方达展望', snippet='易方达展望'),
            dict(url='https://www.efunds.com.cn/a', title='易方达展望', snippet='易方达展望')]
    calls = []
    def read(url, **kw):
        calls.append((url, kw['allowed_domains']))
        return document('https://external.test/a')
    registry, client = setup(profile=profile, rows=rows, reader=read)
    result = search_materials(request(web_read_workers=2), registry=registry)
    assert calls == [('https://www.efunds.com.cn/a', ('www.efunds.com.cn',))]
    assert 'site:www.efunds.com.cn' in client.calls[0][0]
    assert result.coverage[0]['returned_evidence']['text_scope_counts']['extracted_text'] == 0


def test_real_mcp_schema_and_preview(monkeypatch):
    runtime = pytest.importorskip('mcp.server.fastmcp')
    server = runtime.FastMCP('planning-test')
    monkeypatch.setattr(server, 'run', lambda: None)
    monkeypatch.setattr(mcp_server, 'make_fastmcp', lambda _: server)
    mcp_server.run()
    registry, client = setup()
    from ir_search.services import material_search
    monkeypatch.setattr(material_search, 'build_material_registry', lambda: registry)
    async def verify():
        schemas = await server.list_tools()
        schema = next(t.inputSchema for t in schemas if t.name == 'search_materials')
        assert {'dry_run', 'web_institutions', 'web_read_workers'} <= set(schema['properties'])
        response = await server.call_tool('search_materials', request(dry_run=True, web_read_workers=2).to_dict())
        result = json.loads(response[0].text)
        assert result['plan']['execution_mode'] == 'preview' and not result['items'] and not client.calls
    asyncio.run(verify())
