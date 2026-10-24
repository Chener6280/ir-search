"""Regression coverage for the four earlier reviews, with synthetic inputs only."""
from dataclasses import replace
import json
import socket
import threading
import time

import pytest

from ir_search import Diagnostic, MaterialSearchRequest, RequestContext, get_data
from ir_search.context import RequestStopped
from ir_search import mcp_server
from ir_search.infrastructure import public_web
from ir_search.registry import build_data_registry


def test_config_scope_does_not_block_healthy_dataset_or_relax_auth():
    import test_data_source_policy as f
    wind, jydb = f.Upstream('wind_mysql'), f.Upstream('jydb')
    registry = f.registry(wind, jydb)
    registry.diagnostics.append(Diagnostic('source_config_error', 'configure', 'wind_mysql', datasets=('financial_statements',)))
    result = get_data(f.request(), registry=registry)
    assert result.provenance.provider == 'wind_mysql' and len(wind.calls) == 1 and not jydb.calls
    registry.diagnostics.append(Diagnostic('source_credentials_missing', 'configure', 'wind_mysql'))
    result = get_data(f.request(), registry=registry)
    assert result.status.value == 'error' and len(wind.calls) == 1 and not jydb.calls


def test_invalid_currency_keeps_market_registration(tmp_path):
    path = tmp_path / 'synthetic.env'
    path.write_text('WIND_MYSQL_ENABLED=true\nWIND_MYSQL_HOST=db.example.test\nWIND_MYSQL_DATABASE=test\nWIND_MYSQL_USER=test\nWIND_MYSQL_PASSWORD=synthetic\nWIND_MYSQL_FINANCIAL_CURRENCY=bad\n')
    path.chmod(0o600)
    registry = build_data_registry(env_file=path)
    datasets = {cap.dataset for cap, _ in registry.entries()}
    assert {'prices_daily', 'securities', 'fund_nav', 'options_daily'} <= datasets
    assert 'financial_statements' not in datasets
    assert registry.diagnostics[0].datasets == ('financial_statements',)


def test_mock_cannot_look_like_real_official_evidence():
    from ir_search import Query, search, build_registry
    result = search(Query('公司季报', sources=['cninfo']), registry=build_registry(live=False))
    assert result.hits
    for hit in result.hits:
        assert hit.url.startswith('https://mock.invalid/') and '[MOCK' in hit.title
        assert hit.tier.value == 1 and not hit.extra['usable_as_evidence']
        assert hit.evidence_type.value == 'unknown'


def test_core_mcp_registration_retains_legacy_opt_in(monkeypatch):
    pytest.importorskip('mcp')
    registered = []
    class Server:
        def tool(self):
            def register(function):
                registered.append(function.__name__)
                return function
            return register
        def run(self): pass
    monkeypatch.setattr(mcp_server, 'make_fastmcp', lambda cls: Server())
    monkeypatch.setenv('IR_SEARCH_MCP_MODE', 'core')
    mcp_server.run()
    assert set(registered) == set(mcp_server.list_tool_names())
    assert {'get_data', 'search_materials', 'retrieve'} <= set(registered)
    assert 'deep_research' not in registered
    monkeypatch.setenv('IR_SEARCH_MCP_MODE', 'legacy')
    assert 'deep_research' in mcp_server.list_tool_names()


def test_mcp_cannot_grant_private_network_access(monkeypatch):
    monkeypatch.setattr(mcp_server, 'fetch_document_impl', lambda *a, **kw: pytest.fail('must not fetch'))
    result = mcp_server.fetch_document_payload('http://127.0.0.1/', allow_private_network=True)
    assert 'requires local server configuration' in result['errors'][0]


def test_legacy_fetch_uses_pinned_transport_and_one_redirect_budget(monkeypatch):
    from datetime import datetime, timezone
    from ir_search.documents.fetcher import fetch_document
    calls = []
    def transport(url, **kw):
        calls.append((url, kw['context']))
        if len(calls) == 1:
            return public_web._Reply(302, '', 'https://example.org/b', b'', datetime.now(timezone.utc))
        return public_web._Reply(200, 'text/plain', '', b'Example evidence', datetime.now(timezone.utc))
    monkeypatch.setattr(public_web, '_request', transport)
    doc = fetch_document('https://example.org/a')
    assert doc.text == 'Example evidence' and not doc.errors
    assert calls[0][1] is calls[1][1] and calls[0][1].operations == 2


def test_dns_wait_is_cancelled_and_does_not_wait_for_resolver(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def stall(*args, **kwargs):
        entered.set(); release.wait(5)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]
    monkeypatch.setattr(public_web.socket, 'getaddrinfo', stall)
    ctx = RequestContext(timeout_seconds=.1)
    started = time.monotonic()
    try:
        with pytest.raises(RequestStopped, match='deadline_exceeded'):
            public_web._resolve('example.org', 443, ctx)
        assert entered.is_set() and time.monotonic() - started < 1
    finally: release.set()


def test_offline_external_network_is_blocked():
    with pytest.raises(AssertionError, match='offline_external_dns_forbidden'):
        socket.getaddrinfo('external.example.org', 443)
    with socket.socket() as sock:
        with pytest.raises(AssertionError, match='offline_external_network_forbidden'):
            sock.connect(('93.184.216.34', 443))


def test_explicit_unlisted_company_symbol_reaches_material_adapter():
    import test_material_source_isolation as f
    company = '688777.SH'
    row = f.candidate('source_a', symbols=[company], text='第三季度收入改善。')
    source = f.Source('source_a', rows=[row])
    result = f.search([source], question='第三季度收入', symbols=[company], keywords=['收入'])
    assert result.items and result.plan['symbols'] == [company]
    version = result.items[0]['versions'][0]
    assert version['symbols'] == [company]
    assert all(span['text'] == version[span['source_part']][span['start_char']:span['end_char']]
               for span in version['evidence_spans'])


def test_failed_financial_constructor_is_isolated(tmp_path, monkeypatch):
    from ir_search.adapters.financial_statements import FinancialStatementsAdapter
    from ir_search.infrastructure.credentials import SourceConfigError
    def broken(*args, **kwargs): raise SourceConfigError('invalid_source_config')
    monkeypatch.setattr(FinancialStatementsAdapter, '__init__', broken)
    path = tmp_path / 'synthetic.env'
    path.write_text('WIND_MYSQL_ENABLED=true\nWIND_MYSQL_HOST=db.example.test\nWIND_MYSQL_DATABASE=test\nWIND_MYSQL_USER=test\nWIND_MYSQL_PASSWORD=synthetic\n')
    path.chmod(0o600)
    registry = build_data_registry(env_file=path)
    assert 'prices_daily' in {cap.dataset for cap, _ in registry.entries()}
    assert registry.diagnostics[0].datasets == ('financial_statements',)


@pytest.mark.parametrize('value', ['C:cache', '~ir_search_missing_user/cache'])
def test_invalid_cross_platform_path_has_safe_key(value):
    from ir_search.infrastructure.credentials import require_local_path, SourceConfigError
    with pytest.raises(SourceConfigError) as caught:
        require_local_path({'WECHAT_CACHE_DIR': value}, 'WECHAT_CACHE_DIR', allow_relative=True)
    assert caught.value.key == 'WECHAT_CACHE_DIR' and value not in str(caught.value)


def test_persistent_cursor_key_corruption_is_not_silently_replaced(tmp_path):
    from ir_search.infrastructure import pagination
    from ir_search.registry import DataAdapterError
    root = tmp_path / '.local/state'
    root.mkdir(parents=True, mode=0o700)
    path = root / 'cursor.key'
    path.write_text('incomplete'); path.chmod(0o600)
    with pytest.raises(DataAdapterError, match='cursor_state_unavailable'):
        pagination._cursor_key()
    assert path.read_text() == 'incomplete'


def test_legacy_tushare_explicit_proxy_requires_https():
    from ir_search.adapters.tushare import TushareClient, ENDPOINT_DEFAULT
    from ir_search.adapters.base import AdapterError
    assert ENDPOINT_DEFAULT == 'https://api.tushare.pro'
    assert TushareClient('synthetic', 'https://proxy.example.test').endpoint == 'https://proxy.example.test'
    with pytest.raises(AdapterError): TushareClient('synthetic', 'http://proxy.example.test')


def test_native_windows_output_aliases_and_junctions(tmp_path, monkeypatch):
    import os
    if os.name != 'nt': pytest.skip('native Windows paths and junctions')
    import ctypes
    import subprocess
    root = tmp_path / 'exports long directory 中文'
    root.mkdir()
    monkeypatch.setenv('IR_SEARCH_OUTPUT_ROOT', str(root))
    assert mcp_server._mcp_output_dir(str(root).swapcase() + '\\runs', 'audit_dir')
    result = mcp_server.search_materials_payload({'question': 'q'}, audit_dir=r'\\untrusted.invalid\share\runs')
    assert result['diagnostics'][0]['code'] == 'invalid_request'
    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetShortPathNameW(str(root), buffer, len(buffer))
    if length and '~' in buffer.value:
        assert mcp_server._mcp_output_dir(buffer.value + '\\runs', 'audit_dir')
    outside = tmp_path / 'outside'; outside.mkdir()
    link = root / 'junction'
    result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)], capture_output=True)
    if result.returncode: pytest.skip('junction creation unavailable; other Windows paths checked')
    try:
        with pytest.raises(ValueError): mcp_server._mcp_output_dir('junction/runs', 'audit_dir')
    finally: os.rmdir(link)
