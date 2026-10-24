"""Build/install the distributable and exercise it without a source checkout."""
from __future__ import annotations

import ast
import json
from importlib.metadata import version
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_does_not_import_checkout_helpers_or_personal_skill_paths():
    for path in (ROOT / "ir_search").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(alias.name.split(".")[0] not in {"tools", "scripts"} for alias in node.names), path
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                assert (node.module or "").split(".")[0] not in {"tools", "scripts"}, path
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not node.value.startswith(("/Users/", "/home/", "C:\\Users\\")), path
                assert "/desktop/brokerskills" not in node.value.lower(), path


def test_legacy_wechat_wrapper_uses_the_packaged_client():
    from ir_search.clients import wechat_articles
    from ir_search.adapters import dajiala
    from tools import gzh_fetch
    assert gzh_fetch is wechat_articles and dajiala.gzh_fetch is wechat_articles


def test_built_wheel_runs_sdk_search_and_mcp_outside_checkout(tmp_path):
    pytest.importorskip("wheel")
    if int(version("setuptools").split(".")[0]) < 61:
        pytest.skip("Building pyproject metadata requires setuptools>=61; verified in the modern packaging environment")
    project = tmp_path / "source"
    project.mkdir()
    for name in ("pyproject.toml", "README.md"):
        shutil.copy2(ROOT / name, project / name)
    shutil.copytree(ROOT / "ir_search", project / "ir_search", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # These files simulate local private inputs; none belongs in the wheel.
    (project / "credentials.env").write_text("PRIVATE_KEY=package_test_sentinel\n", encoding="utf-8")
    (project / "accounts.json").write_text('{"private":"package_test_sentinel"}', encoding="utf-8")
    (project / ".local").mkdir()
    (project / ".local" / "wechat_accounts.json").write_text('[{"name":"package_test_sentinel"}]', encoding="utf-8")
    wheels, installed, unrelated = tmp_path / "wheels", tmp_path / "installed", tmp_path / "unrelated"
    unrelated.mkdir()
    clean_env = {key: value for key, value in os.environ.items() if key in {
        "PATH", "SYSTEMROOT", "WINDIR", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL",
    }}
    clean_env.update(PYTHONUTF8="1", PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_CACHE_DIR="1")
    for command in (
        [sys.executable, "-m", "pip", "wheel", "--no-index", "--no-deps", "--no-build-isolation", "--wheel-dir", str(wheels), str(project)],
        [sys.executable, "-m", "pip", "install", "--no-index", "--no-deps", "--target", str(installed), "WHEEL"],
    ):
        if command[-1] == "WHEEL":
            command[-1] = str(next(wheels.glob("*.whl")))
        result = subprocess.run(command, cwd=unrelated, env=clean_env, text=True, encoding="utf-8", capture_output=True, timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
    with zipfile.ZipFile(next(wheels.glob("*.whl"))) as archive:
        names = archive.namelist()
        assert "ir_search/configs/source_routes.yaml" in names
        assert "ir_search/entities/a_share_companies.csv" in names
        assert "ir_search/entities/institutions.csv" in names
        assert "ir_search/clients/wechat_articles.py" in names
        assert not any(name.endswith(".env") or "accounts.json" in name or name.startswith(("tools/", "docs/")) for name in names)
        assert not any(b"package_test_sentinel" in archive.read(name) for name in names)
        entrypoints = archive.read(next(name for name in names if name.endswith("entry_points.txt"))).decode()
        assert "ir-search-mcp = ir_search.mcp_server:run" in entrypoints
        assert "ir-search-acceptance = ir_search.acceptance:main" in entrypoints
        assert "ir-search-gangtise-login = ir_search.infrastructure.gangtise_auth:main" in entrypoints
        assert "ir-search-doctor = ir_search.services.source_diagnostics:main" in entrypoints
        assert 'ir_search/services/source_diagnostics.py' in names
        assert 'ir_search/services/material_runs.py' in names
        assert 'ir_search/infrastructure/recovery.py' in names
        assert 'ir_search/infrastructure/web_toolkit.py' in names
        assert 'ir_search/infrastructure/rss.py' in names
        assert "ir_search/adapters/akshare_futures.py" in names
        for name in ('financial_statements','domestic_derivatives','akshare_options','fmp','tushare_corpus','web_materials','zsxq_materials','wechat_materials','ima_materials','wisburg_materials','platform_materials','alphapai_materials','gangtise_materials','xhs_materials','rss_materials'):
            assert 'ir_search/adapters/'+name+'.py' in names
        assert 'ir_search/contracts/market.py' in names
        assert 'ir_search/infrastructure/calendars.py' in names
        assert 'ir_search/infrastructure/fmp.py' in names
        assert 'ir_search/infrastructure/tushare_corpus.py' in names
        assert 'ir_search/infrastructure/public_web.py' in names
        assert 'ir_search/infrastructure/web_search.py' in names
        assert 'ir_search/infrastructure/web_routing.py' in names
        assert 'ir_search/documents/wechat_html.py' in names
        assert 'ir_search/infrastructure/private_files.py' in names
        assert 'ir_search/infrastructure/xhs.py' in names
        assert 'ir_search/infrastructure/xhs_documents.py' in names
        assert 'ir_search/services/material_archive.py' in names
        for name in ('mcp_protocol','zsxq','zsxq_documents','wechat','wechat_cache','ima','ima_documents','wisburg','wisburg_documents','platform_documents','community_documents','video_documents','alphapai','alphapai_cache','alphapai_documents','_alphapai_worker','gangtise','gangtise_auth','_gangtise_auth_worker','gangtise_documents'):
            assert 'ir_search/infrastructure/'+name+'.py' in names
        assert 'ir_search/contracts/overseas.py' in names
        for name in ('contracts/materials.py', 'material_registry.py', 'services/material_search.py', 'adapters/jydb_materials.py'):
            assert 'ir_search/' + name in names
    # Delete the build source so incidental imports cannot mask a missing file.
    shutil.rmtree(project)
    env_file = unrelated / "credentials.env"
    env_file.write_text("# no live credentials\n", encoding="utf-8")
    env_file.chmod(0o600)
    clean_env.update(PYTHONPATH=str(installed), IR_SEARCH_CREDENTIALS_FILE=str(env_file), IR_SEARCH_LIVE="0")
    script = r'''
import asyncio, importlib.util, json, os, socket, sys
from pathlib import Path
# Windows initializes asyncio's self-pipe using a loopback socket pair.
# Create it before the guard; all application/provider connections stay blocked.
event_loop = asyncio.new_event_loop()
def no_network(*args, **kwargs):
    raise AssertionError("Offline package verification attempted network access")
socket.socket.connect = no_network
import ir_search
from ir_search.config_validation import validate_configs
from ir_search.entity import load_entities
assert len(ir_search.list_institutions()['institutions']) == 14
doctor = ir_search.diagnose_sources(['xhs','video'])
assert doctor['source_calls_started'] == 0
assert all(not s['search_live_verified'] for s in doctor['sources'])
empty_run = ir_search.search_materials(ir_search.MaterialSearchRequest('demand'))
run_record = ir_search.record_material_run(empty_run, Path('private-runs').absolute())
assert run_record['status'] == 'recorded'
assert ir_search.next_material_request(empty_run) is None
from ir_search.adapters.xhs_materials import XhsMaterialAdapter
from ir_search.infrastructure.xhs import XhsProfile
from ir_search.infrastructure.xhs_documents import fetch_xhs_document
xhs_registry = ir_search.MaterialRegistry()
xhs_registry.register(XhsMaterialAdapter(XhsProfile(token='package-test-xhs-token')))
xhs_preview = ir_search.search_materials(ir_search.MaterialSearchRequest(
    'channel feedback', providers=['xhs'], published_start='2026-09-01', published_end='2026-09-18',
    xhs_sort='latest', xhs_comment_limit=2, dry_run=True), registry=xhs_registry).to_dict()
assert xhs_preview['plan']['source_calls_started'] == 0
assert xhs_preview['plan']['selected_providers'] == ['xhs']
assert xhs_preview['plan']['source_plans'][0]['comment_limit_per_note'] == 2
from ir_search.adapters.platform_materials import XueqiuMaterialAdapter, EastmoneyMaterialAdapter, VideoMaterialAdapter, XiaoyuzhouMaterialAdapter
from ir_search.infrastructure.credentials import WebMaterialProfile
platform_registry = ir_search.MaterialRegistry()
for cls in (XueqiuMaterialAdapter, EastmoneyMaterialAdapter, VideoMaterialAdapter, XiaoyuzhouMaterialAdapter):
    platform_registry.register(cls(WebMaterialProfile(allow_anonymous=True)))
platform_preview = ir_search.search_materials(ir_search.MaterialSearchRequest(
    'NVIDIA earnings', providers=['video'], published_start='2026-09-01', published_end='2026-09-17',
    video_platforms=['youtube'], video_languages=['en'], dry_run=True), registry=platform_registry).to_dict()
assert platform_preview['plan']['source_calls_started'] == 0
assert platform_preview['plan']['source_plans'][0]['queries'][0]['platform'] == 'youtube'
audio_preview = ir_search.search_materials(ir_search.MaterialSearchRequest(
    'podcast AI', providers=['xiaoyuzhou'], material_types=['audio'], published_start='2026-09-01',
    published_end='2026-09-17', dry_run=True), registry=platform_registry).to_dict()
assert audio_preview['plan']['source_plans'][0]['audio_transcription'] == 'never_during_search'
from ir_search.infrastructure.audio_asr import audio_configuration_status
assert not audio_configuration_status()['configured']
from ir_search import mcp_server
from ir_search.adapters.web_materials import WebMaterialAdapter
from ir_search.infrastructure.web_search import ExaWebClient
assert ir_search.WebSearchFallback.__module__ == 'ir_search.contracts.materials'
class ExhaustedSearch:
    def search(self, *args, **kwargs):
        raise ir_search.DataAdapterError('quota')
web_registry = ir_search.MaterialRegistry()
web_profile = WebMaterialProfile(search_provider='regional', overseas_provider='exa', exa_api_key='package-test-exa-key')
assert isinstance(ExaWebClient(web_profile), ExaWebClient)
web_registry.register(WebMaterialAdapter(web_profile, exa_client=ExhaustedSearch()))
web_request = ir_search.MaterialSearchRequest('cloud revenue', keywords=['revenue'], providers=['web'],
    web_region='overseas', published_start='2025-01-01', published_end='2025-12-31', text_reads_per_source=0)
web_result = ir_search.search_materials(web_request, registry=web_registry).to_dict()
assert web_result['status'] == 'unavailable' and not web_result['items']
assert web_result['fallback_requests'][0]['action'] == 'caller_web_search'
assert web_result['fallback_requests'][0]['failed_provider'] == 'exa'
assert mcp_server.search_materials_payload(web_request.to_dict(), registry=web_registry)['fallback_requests'] == web_result['fallback_requests']
assert 'native web search' in mcp_server.MCP_INSTRUCTIONS
from ir_search.acceptance import run_data_acceptance
from ir_search.adapters.akshare_futures import AKShareFuturesAdapter
assert AKShareFuturesAdapter().capabilities[0].dataset == "futures_intraday"
from ir_search.adapters.financial_statements import FinancialStatementsAdapter
from ir_search.adapters.domestic_derivatives import DomesticDerivativesAdapter
from ir_search.adapters.akshare_options import AKShareOptionsAdapter
from ir_search.infrastructure.credentials import MySQLProfile
profile=MySQLProfile('wind_mysql','test-host','test-db','test-user','package_test_only',tls_mode='disabled')
assert FinancialStatementsAdapter(profile).capabilities[0].dataset=='financial_statements'
assert len(DomesticDerivativesAdapter(profile).capabilities)==5
assert AKShareOptionsAdapter().capabilities[0].dataset=='options_intraday'
from ir_search.adapters.fmp import FMPAdapter
from ir_search.infrastructure.credentials import FMPProfile, source_configuration_status
fmp_adapter = FMPAdapter(FMPProfile('package_test_fmp_key'))
assert len(fmp_adapter.capabilities) == 3
assert ir_search.describe_dataset('prices_daily_basic')['status'] == 'ok'
assert ir_search.describe_dataset('financial_statements_standardized')['status'] == 'ok'
assert next(s for s in source_configuration_status()['sources'] if s['provider']=='fmp')['enabled'] is False
assert not run_data_acceptance(live=False)["live_requested"]
assert str(Path(ir_search.__file__).resolve()).startswith(sys.path[1])
assert importlib.util.find_spec("tools") is None
assert validate_configs() == [] and load_entities()
result = ir_search.search(ir_search.Query("中际旭创 一季报", sources=["cninfo"]), registry=ir_search.build_registry(live=False))
assert result.hits and result.diagnostics
assert result.diagnostics[0].adapter_mode == "mock"
health = mcp_server.source_health_payload()
assert "legacy_health_unavailable" not in json.dumps(health)
assert health["sources"]
data = ir_search.get_data(ir_search.DataRequest("securities")).to_dict()
assert data["status"] == "unavailable" and data["diagnostics"]
assert "tools.gzh_fetch" not in sys.modules
materials = ir_search.search_materials(ir_search.MaterialSearchRequest("贵州茅台9月动销")).to_dict()
assert materials['plan']['symbols'] == ['600519.SH']
assert materials['required_inputs'] == ['published_start', 'published_end', 'period_start', 'period_end', 'providers']
assert materials['diagnostics'] and materials['source_text_trust'] == 'untrusted'
assert materials['plan']['source_selection_policy'] == 'explicit_user_choice'
assert materials['plan']['source_calls_started'] == 0
assert ir_search.list_capabilities()['materials']['capabilities'] == []
assert ir_search.list_capabilities()['web_reading']['modes'] == ['http', 'auto', 'browser', 'scrapling', 'firecrawl']
from ir_search.infrastructure import web_documents, web_browser
from ir_search.infrastructure._browser_network import BrowserNetwork
from ir_search.infrastructure._crawl_worker import _launch_options
assert _launch_options('http://127.0.0.1:1')['chromium_sandbox'] is True
assert {s['provider'] for s in ir_search.list_capabilities()['materials']['unregistered_sources']} >= {'tushare_corpus', 'ima'}
bounded_materials = ir_search.search_materials(ir_search.MaterialSearchRequest(
    '贵州茅台动销', published_start='2025-09-01', published_end='2025-10-15', providers=['ima'])).to_dict()
assert bounded_materials['coverage'][0]['state'] == 'not_registered'
assert bounded_materials['coverage'][0]['returned_evidence']['text_scope_counts'] == {'metadata':0,'abstract':0,'extracted_text':0,'search_snippet':0,'source_excerpt':0}
assert not any(name == 'ir_search.research' or name.startswith('ir_search.research.') for name in sys.modules)
mcp_checked = False
if importlib.util.find_spec("mcp"):
    from mcp.server.fastmcp import FastMCP
    server = FastMCP("package-test")
    server.run = lambda: None
    mcp_server.make_fastmcp = lambda _: server
    mcp_server.run()
    async def check():
        assert [tool.name for tool in await server.list_tools()] == mcp_server.list_tool_names()
        assert 'compatibility-only' in next(tool.description for tool in await server.list_tools() if tool.name == 'deep_research').lower()
        response = await server.call_tool("list_capabilities", {})
        catalog = json.loads(response[0].text)
        assert catalog["source_policy"]["historical_intraday_provider"] is None
        assert catalog['materials']['capabilities'] == []
        response = await server.call_tool('search_materials', {'question': '贵州茅台9月动销'})
        material_result = json.loads(response[0].text)
        assert material_result['required_inputs'] == materials['required_inputs'] and material_result['diagnostics']
        response = await server.call_tool('source_health', {'providers':['xhs']})
        assert json.loads(response[0].text)['operational_status']['source_calls_started'] == 0
        response = await server.call_tool('search_materials', {'question':'demand', 'audit_dir':'mcp-runs'})
        assert json.loads(response[0].text)['audit']['status'] == 'recorded'
        response = await server.call_tool("get_data", {"dataset":"securities"})
        assert json.loads(response[0].text)["status"] == "unavailable"
    event_loop.run_until_complete(check())
    mcp_checked = True
Path(os.environ['IR_SEARCH_CREDENTIALS_FILE']).write_text('FMP_ENABLED=true\nFMP_API_KEY=package_test_fmp_key\n')
catalog = ir_search.list_capabilities()
assert len(catalog['capabilities']) == 3 and all(c['provider']=='fmp' for c in catalog['capabilities'])
assert len(mcp_server.list_capabilities_payload()['capabilities']) == 3
rejected = ir_search.get_data(ir_search.DataRequest('securities', market='US')).to_dict()
assert not rejected['records'] and any(d['code']=='unsupported' for d in rejected['diagnostics'])
assert next(s for s in source_configuration_status()['sources'] if s['provider']=='fmp')['enabled'] is True
Path(os.environ['IR_SEARCH_CREDENTIALS_FILE']).write_text('TUSHARE_CORPUS_ENABLED=true\nTUSHARE_MCP_TOKEN=package_test_corpus_key\n')
corpus_registry = ir_search.build_material_registry()
assert [a.name for a in corpus_registry.entries()] == ['tushare_corpus']
assert ir_search.list_capabilities()['materials']['capabilities'][0]['provider'] == 'tushare_corpus'
from ir_search.infrastructure.tushare_corpus import TushareCorpusClient, _RPCResponse
def offline_corpus(profile, payload, **kwargs):
    if payload['method']=='notifications/initialized': return _RPCResponse({})
    result = {'protocolVersion':'2025-03-26'} if payload['method']=='initialize' else {
        'content':[{'type':'text','text':json.dumps([{'title':'贵州茅台动销', 'trade_date':'20250401',
        'ts_code':'600519.SH','inst_csname':'测试机构','abstr':'贵州茅台动销观察。','report_code':'fixture'}])}], 'isError':False}
    return _RPCResponse({'jsonrpc':'2.0','id':payload['id'],'result':result})
corpus_registry.entries()[0]._client_factory = lambda profile: TushareCorpusClient(profile, transport=offline_corpus)
corpus_request=ir_search.MaterialSearchRequest('贵州茅台动销', published_start='2025-04-01',published_end='2025-04-01', material_types=['research_report'],providers=['tushare_corpus'])
corpus_result=ir_search.search_materials(corpus_request,registry=corpus_registry).to_dict()
assert corpus_result['items'][0]['versions'][0]['text_scope']=='abstract'
assert corpus_result['coverage'][0]['scans'][0]['received_count']==1
Path(os.environ['IR_SEARCH_CREDENTIALS_FILE']).write_text('WEB_MATERIALS_ENABLED=true\nWEB_ALLOW_ANONYMOUS=true\n')
web_registry = ir_search.build_material_registry()
assert [a.name for a in web_registry.entries()] == ['web']
from ir_search.infrastructure.web_search import WebSearchPage
from ir_search.documents.html import extract_html_document
from datetime import datetime, timezone
class OfflineWebClient:
    def search(self, query, **kwargs):
        return WebSearchPage([{'url':'https://example.gov.cn/a','title':'智能家居消费政策通知','snippet':'智能家居发现摘要'}],datetime.now(timezone.utc))
def offline_reader(url, **kwargs):
    document = extract_html_document('<title>智能家居消费政策通知</title><meta name="pubdate" content="2026-09-14"><p>智能家居政策明确了适用范围。</p>'.encode(),url)
    document.extra['adapter_mode']='live'
    return document
web_registry.entries()[0]._client=OfflineWebClient()
web_registry.entries()[0]._reader=offline_reader
web_request=ir_search.MaterialSearchRequest('智能家居',keywords=['智能家居'],providers=['web'],published_start='2026-09-01',published_end='2026-09-15')
from dataclasses import replace
preview_context = ir_search.RequestContext(max_operations=10)
preview = ir_search.search_materials(replace(web_request, dry_run=True, web_institutions=('csrc',), web_read_workers=2, web_read_mode='http'),
    registry=web_registry, context=preview_context)
assert preview_context.operations == 0 and not preview.items
assert preview.plan['source_plans'][0]['allowed_domains'] == ['csrc.gov.cn']
assert preview.plan['source_plans'][0]['read_workers'] == 2
web_result=ir_search.search_materials(web_request,registry=web_registry).to_dict()
assert web_result['items'][0]['versions'][0]['text_scope']=='extracted_text'
assert web_result['items'][0]['versions'][0]['discovery_provider']=='anysearch'
assert web_result['coverage'][0]['scans'][0]['date_filter_basis']=='local_publication_metadata'
web_documents.fetch_public_document = offline_reader
retrieved_web = ir_search.retrieve(ir_search.MaterialRequest('智能家居', ['https://example.gov.cn/a'], web_read_mode='http')).to_dict()
web_material = retrieved_web['materials'][0]
assert web_material['read_details']['backend'] == 'http' and web_material['text_hash']
assert all(s['text'] == web_material['text'][s['start_char']:s['end_char']] for s in web_material['evidence_spans'])
if mcp_checked:
    async def check_web_reader():
        response = await server.call_tool('retrieve', {'question':'智能家居', 'urls':['https://example.gov.cn/a'], 'web_read_mode':'http'})
        result = json.loads(response[0].text)
        assert result['materials'][0]['text_hash'] == web_material['text_hash']
        assert result['reads'][0]['backend'] == 'http'
    event_loop.run_until_complete(check_web_reader())
Path(os.environ['IR_SEARCH_CREDENTIALS_FILE']).write_text('WEB_MATERIALS_ENABLED=true\nWEB_SEARCH_PROVIDER=regional\nBOCHA_API_KEY=package_bocha_key\nANYSEARCH_API_KEY=package_anysearch_key\n')
regional_registry=ir_search.build_material_registry()
regional_adapter=regional_registry.entries()[0]
from ir_search.infrastructure.web_search import BochaWebClient
def offline_bocha(url,**kwargs):
    from ir_search.infrastructure.public_web import _Reply
    assert url=='https://api.bochaai.com/v1/web-search'
    assert kwargs['headers']['Authorization']=='Bearer package_bocha_key'
    return _Reply(200,'application/json','',json.dumps({'code':200,'data':{'webPages':{'value':[
        {'name':'智能家居政策','url':'https://example.gov.cn/policy','snippet':'智能家居政策'}]}}}).encode(),datetime.now(timezone.utc))
regional_adapter._bocha_client=BochaWebClient(regional_adapter._profile,transport=offline_bocha)
regional_adapter._reader=offline_reader
regional_request=ir_search.MaterialSearchRequest('智能家居',keywords=['智能家居'],providers=['web'],web_region='cn',published_start='2026-09-01',published_end='2026-09-16')
regional_result=ir_search.search_materials(regional_request,registry=regional_registry).to_dict()
assert regional_result['items'][0]['versions'][0]['discovery_provider']=='bocha'
assert regional_result['coverage'][0]['scans'][0]['web_region']=='cn'
if mcp_checked:
    from ir_search.services import material_search
    material_search.build_material_registry=lambda:regional_registry
    async def check_regional():
        response=await server.call_tool('search_materials',regional_request.to_dict())
        assert json.loads(response[0].text)['items'][0]['versions'][0]['discovery_provider']=='bocha'
    event_loop.run_until_complete(check_regional())
Path(os.environ['IR_SEARCH_CREDENTIALS_FILE']).write_text('ZSXQ_MATERIALS_ENABLED=true\nZSXQ_KEY=package_test_zsxq_key\nZSXQ_GROUP_IDS=100\n')
zsxq_registry=ir_search.build_material_registry()
assert [a.name for a in zsxq_registry.entries()]==['zsxq']
assert ir_search.list_capabilities()['materials']['capabilities'][0]['provider']=='zsxq'
from ir_search.infrastructure.zsxq import ZsxqClient, _RPCResponse as ZsxqRPC
from ir_search.infrastructure.zsxq_documents import fetch_zsxq_document
zsxq_topic={'topic_id':'101','group':{'group_id':'100','name':'Synthetic group'},'type':'talk',
    'content':'Company demand is growing.','owner':{'name':'Synthetic author'},'create_time':'2026-09-14T10:00:00+0800',
    'files':[{'file_id':'501','name':'Demand.pdf','size':100}]}
def offline_zsxq(profile,payload,**kwargs):
    if payload['method']=='notifications/initialized': return ZsxqRPC({})
    if payload['method']=='initialize':result={'protocolVersion':'2024-11-05'}
    else:
        name=payload['params']['name']
        assert name in {'get_group_topics','get_topic_info'}
        data={'success':True,**({'topics_brief':[zsxq_topic],'has_more':False,'next_end_time':''}
            if name=='get_group_topics' else {'topic':zsxq_topic})}
        result={'content':[{'type':'text','text':json.dumps(data)}]}
    return ZsxqRPC({'jsonrpc':'2.0','id':payload['id'],'result':result})
zsxq_registry.entries()[0]._client_factory=lambda profile:ZsxqClient(profile,transport=offline_zsxq)
zsxq_request=ir_search.MaterialSearchRequest('Company demand',keywords=['demand'],providers=['zsxq'],published_start='2026-09-01',published_end='2026-09-15')
zsxq_result=ir_search.search_materials(zsxq_request,registry=zsxq_registry).to_dict()
zsxq_version=zsxq_result['items'][0]['versions'][0]
assert zsxq_version['text_scope']=='extracted_text' and zsxq_version['sections'][0]['author']=='Synthetic author'
assert zsxq_version['attachments'][0]['source_ref']=='zsxq://file/100/101/501'
profile=zsxq_registry.entries()[0]._profile
doc=fetch_zsxq_document('zsxq://topic/100/101',context=ir_search.RequestContext(),profile=profile,client=ZsxqClient(profile,transport=offline_zsxq))
assert doc.text==zsxq_version['text'] and doc.extra['sections']
if mcp_checked:
    from ir_search.services import material_search
    material_search.build_material_registry=lambda:zsxq_registry
    async def check_zsxq():
        response=await server.call_tool('search_materials',{'question':'Company demand','keywords':['demand'],'providers':['zsxq'],
            'published_start':'2026-09-01','published_end':'2026-09-15'})
        result=json.loads(response[0].text)
        assert result['items'][0]['versions'][0]['source_ref']=='zsxq://topic/100/101'
    event_loop.run_until_complete(check_zsxq())
private_accounts=Path('wechat_accounts.json')
private_accounts.write_text('[{"name":"Synthetic research","ghid":"gh_synthetic"}]')
private_accounts.chmod(0o600)
Path(os.environ['IR_SEARCH_CREDENTIALS_FILE']).write_text('WECHAT_MATERIALS_ENABLED=true\nDAJIALA_KEY=package_wechat_key\nWECHAT_ACCOUNTS_FILE=wechat_accounts.json\n')
wechat_registry=ir_search.build_material_registry()
assert [a.name for a in wechat_registry.entries()]==['wechat']
from ir_search.infrastructure.wechat import DajialaMaterialClient, fetch_wechat_document
wechat_url='https://mp.weixin.qq.com/s?__biz=MzTestAA%3D%3D&mid=123&idx=1&sn='+'a'*32
def offline_wechat(url,**kwargs):
    from ir_search.infrastructure.public_web import _Reply
    if url.startswith('https://mp.weixin.qq.com/'):
        assert 'body' not in kwargs and 'headers' not in kwargs
        return _Reply(200,'text/html','',b'<title>challenge</title><p>captcha</p>',datetime.now(timezone.utc))
    assert url.startswith('https://www.dajiala.com/')
    body=json.loads(kwargs['body'])
    assert body['key']=='package_wechat_key'
    if url.endswith('/post_history'):
        result={'code':0,'data':[{'url':wechat_url,'title':'Company demand','digest':'Demand improved','post_time_str':'2026-09-15 12:00:00'}],
            'nickname':'Synthetic research','ghid':'gh_synthetic','is_end':1}
    else:
        assert url.endswith('/article_html')
        result={'code':0,'data':{'article_url':wechat_url,'title':'Company demand','nickname':'Synthetic research',
            'post_time_str':'2026-09-15 12:00:00','html':'<html><body><p>Company demand improved this month.</p></body></html>'}}
    return _Reply(200,'application/json','',json.dumps(result).encode(),datetime.now(timezone.utc))
wechat_adapter=wechat_registry.entries()[0]
wechat_adapter._client=DajialaMaterialClient(wechat_adapter._profile,transport=offline_wechat)
wechat_adapter._reader=lambda url,**kw:fetch_wechat_document(url,transport=offline_wechat,**kw)
wechat_request=ir_search.MaterialSearchRequest('Company demand',keywords=['demand'],providers=['wechat'],wechat_accounts=['Synthetic research'],published_start='2026-09-01',published_end='2026-09-16')
wechat_result=ir_search.search_materials(wechat_request,registry=wechat_registry).to_dict()
assert wechat_result['items'][0]['versions'][0]['text_provider']=='dajiala'
assert wechat_result['items'][0]['versions'][0]['collection_id']=='gh_synthetic'
wechat_again=ir_search.search_materials(wechat_request,registry=wechat_registry).to_dict()
assert wechat_again['items'][0]['versions'][0]['read_details']['cache_state']=='hit'
assert wechat_again['coverage'][0]['scans'][0]['cache_state']=='hit'
assert wechat_again['items'][0]['versions'][0]['read_details']['vendor_body_calls']==0
assert (Path(os.environ['IR_SEARCH_CREDENTIALS_FILE']).parent/'.local/wechat-cache').is_dir()
if mcp_checked:
    from ir_search.services import material_search
    material_search.build_material_registry=lambda:wechat_registry
    async def check_wechat():
        response=await server.call_tool('search_materials',wechat_request.to_dict())
        assert json.loads(response[0].text)['items'][0]['versions'][0]['channel']=='wechat'
        response=await server.call_tool('retrieve',{'question':'demand','urls':[wechat_url],'wechat_cache_mode':'use',
            'archive_dir':'local-archive'})  # relative to the MCP output root
        cached=json.loads(response[0].text)['materials'][0]
        assert cached['read_details']['cache_state']=='hit' and cached['text_provider']=='dajiala'
        assert cached['article']['blocks'] and cached['archive']['status']=='ok'
        assert Path(cached['archive']['directory'],'material.json').is_file()
    event_loop.run_until_complete(check_wechat())
Path(os.environ['IR_SEARCH_CREDENTIALS_FILE']).write_text('IMA_MATERIALS_ENABLED=true\nIMA_API_KEY=package_ima_key\nIMA_CLIENT_ID=package_ima_client\nIMA_KNOWLEDGE_BASE_IDS=kb1\n')
ima_registry=ir_search.build_material_registry()
assert [a.name for a in ima_registry.entries()]==['ima']
from ir_search.infrastructure.ima import IMAClient
from ir_search.infrastructure.ima_documents import fetch_ima_document
from ir_search.infrastructure import ima_documents
from ir_search.infrastructure.public_web import _Reply
def offline_ima(url,**kw):
    assert url.startswith('https://ima.qq.com/openapi/')
    assert kw['headers']['ima-openapi-clientid']=='package_ima_client'
    kw['context'].begin_operation()
    operation=url.rsplit('/',1)[-1]
    data={'search_knowledge':{'info_list':[{'media_id':'m1','title':'Company demand','highlight_content':'Company demand improved','media_type':11}]},
          'search_note':{'search_note_infos':[],'is_end':True},
          'get_media_info':{'media_type':11,'notebook_ext_info':{'notebook_id':'n1'}},
          'get_doc_content':{'content':'Company demand improved; this is an unverified research observation.'}}[operation]
    return _Reply(200,'application/json','',json.dumps({'code':0,'data':data}).encode(),datetime.now(timezone.utc))
ima_adapter=ima_registry.entries()[0]
ima_adapter._client=IMAClient(ima_adapter._profile,transport=offline_ima)
ima_request=ir_search.MaterialSearchRequest('Company demand',keywords=['demand'],providers=['ima'],ima_include_notes=False,
    ima_knowledge_base_ids=['kb1'],published_start='2026-09-01',published_end='2026-09-16')
ima_result=ir_search.search_materials(ima_request,registry=ima_registry).to_dict()
assert ima_result['items'][0]['versions'][0]['text_scope']=='extracted_text'
assert ima_result['items'][0]['versions'][0]['provenance']['authority']=='unknown'
ima_documents.IMAClient=lambda p:ima_adapter._client
ima_bundle=ir_search.retrieve(ir_search.MaterialRequest(question='demand',urls=['ima://media/m1'])).to_dict()
assert ima_bundle['materials'][0]['text_provider']=='ima'
assert next(s for s in source_configuration_status()['sources'] if s['provider']=='ima')['configured']
if mcp_checked:
    material_search.build_material_registry=lambda:ima_registry
    async def check_ima():
        response=await server.call_tool('search_materials',ima_request.to_dict())
        assert json.loads(response[0].text)['items'][0]['versions'][0]['source_ref']=='ima://media/m1'
        response=await server.call_tool('retrieve',{'question':'demand','urls':['ima://media/m1']})
        assert json.loads(response[0].text)['materials'][0]['text_provider']=='ima'
    event_loop.run_until_complete(check_ima())
Path(os.environ['IR_SEARCH_CREDENTIALS_FILE']).write_text('WISBURG_MATERIALS_ENABLED=true\nWISBURG_API_KEY=package_wisburg_key\n')
wisburg_registry=ir_search.build_material_registry()
assert [a.name for a in wisburg_registry.entries()]==['wisburg']
from ir_search.infrastructure import wisburg, wisburg_documents
def offline_wisburg(profile,payload,**kwargs):
    if payload['method']=='notifications/initialized':return wisburg._RPCResponse({})
    if payload['method']=='initialize': result={'protocolVersion':'2024-11-05'}
    else:
        tool=payload['params']['name']
        body=('Found 1 reports:\n\n[101] Company demand\n  date: 2026-09-17T08:00:00+08:00\n' if tool.startswith('list-') else
              '# Company demand\n\n- ID: 101\n- Date: 2026-09-17T08:00:00+08:00\n\n## Summary\n\nCompany demand outlook is a stored supplier summary.')
        result={'content':[{'type':'text','text':body}]}
    return wisburg._RPCResponse({'jsonrpc':'2.0','id':payload['id'],'result':result})
wisburg._post=offline_wisburg
wisburg_request=ir_search.MaterialSearchRequest('Company demand',keywords=['demand'],providers=['wisburg'],
    wisburg_categories=['company'],published_start='2026-09-01',published_end='2026-09-17')
wisburg_result=ir_search.search_materials(wisburg_request,registry=wisburg_registry).to_dict()
assert wisburg_result['items'][0]['versions'][0]['text_scope']=='abstract'
assert wisburg_result['items'][0]['versions'][0]['provenance']['generated']
wisburg_bundle=ir_search.retrieve(ir_search.MaterialRequest('demand',['wisburg://report/101'])).to_dict()
assert wisburg_bundle['materials'][0]['text_origin']=='provider_summary'
assert next(s for s in source_configuration_status()['sources'] if s['provider']=='wisburg')['configured']
if mcp_checked:
    material_search.build_material_registry=lambda:wisburg_registry
    async def check_wisburg():
        tools=await server.list_tools()
        assert 'wisburg_categories' in next(t for t in tools if t.name=='search_materials').inputSchema['properties']
        response=await server.call_tool('search_materials',wisburg_request.to_dict())
        assert json.loads(response[0].text)['items'][0]['versions'][0]['text_scope']=='abstract'
        response=await server.call_tool('retrieve',{'question':'demand','urls':['wisburg://report/101'],
            'archive_dir':'wisburg-archive'})  # relative to the MCP output root
        m=json.loads(response[0].text)['materials'][0]
        assert m['text_origin']=='provider_summary' and m['archive']['status']=='ok'
        assert all(s['text']==m['text'][s['start_char']:s['end_char']] for s in m['evidence_spans'])
    event_loop.run_until_complete(check_wisburg())
# New feed discovery must work entirely from the installed wheel, including MCP.
from ir_search.infrastructure import rss
from ir_search.infrastructure.web_toolkit import web_toolkit_status
from ir_search.infrastructure.rss import RSSProfile
from ir_search.adapters.rss_materials import RSSMaterialAdapter
Path(os.environ['IR_SEARCH_CREDENTIALS_FILE']).write_text('RSS_ENABLED=true\nRSS_FEED_URLS=\'["https://example.org/feed.xml"]\'\n')
feed_xml=b'<rss version="2.0"><channel><item><title>Revenue</title><link>https://example.org/report</link><description>Revenue 123</description><pubDate>Fri, 18 Sep 2026 00:00:00 +0000</pubDate></item></channel></rss>'
rss.fetch_feed=lambda url,**kw:rss.parse_feed(feed_xml,url,fetched_at=datetime.now(timezone.utc))
# The adapter resolves its default client from the module import; patch that alias too.
import ir_search.adapters.rss_materials as rss_adapter
rss_adapter.fetch_feed=rss.fetch_feed
from ir_search.services import material_search
material_search.build_material_registry=ir_search.build_material_registry
rss_request=ir_search.MaterialSearchRequest('Revenue',providers=['rss'],published_start='2026-09-01',published_end='2026-09-18',text_reads_per_source=0)
feed_result=ir_search.search_materials(rss_request).to_dict()
assert feed_result['items'], json.dumps(feed_result)
assert feed_result['items'][0]['versions'][0]['text_scope']=='source_excerpt'
assert feed_result['items'][0]['versions'][0]['source_ref'].startswith('https://example.org/feed.xml#entry-')
assert web_toolkit_status()['firecrawl']['state']=='disabled'
assert ir_search.diagnose_sources(['rss'])['sources'][0]['state']=='configured_unverified'
if mcp_checked:
    async def check_rss():
        response=await server.call_tool('search_materials',rss_request.to_dict())
        v=json.loads(response[0].text)['items'][0]['versions'][0]
        assert v['channel']=='feed' and v['text_provider']=='rss'
    event_loop.run_until_complete(check_rss())
# Expanded datasets, catalogs and MCP must load from the wheel with no source checkout.
from ir_search.adapters.funds import FundAdapter
from ir_search.adapters.global_macro import GlobalMacroAdapter
from ir_search.adapters.fiona import FionaAdapter
from ir_search.infrastructure.fiona import FionaProfile
from ir_search.infrastructure.official_materials import _issuers
from ir_search.adapters.official_materials import HKEXMaterialAdapter, CompanyIRMaterialAdapter
from ir_search.infrastructure.public_web import _Reply
assert len(_issuers())==3
assert len(FundAdapter(MySQLProfile('wind_mysql','test-host','test-db','test-user','package_test_only')).capabilities)==5
assert len(FionaAdapter(FionaProfile('package-fiona-test')).capabilities)==4
Path(os.environ['IR_SEARCH_CREDENTIALS_FILE']).write_text('GLOBAL_MACRO_ENABLED=true\nHKEX_ENABLED=true\nCOMPANY_IR_ENABLED=true\n')
import ir_search.adapters.global_macro as macro_module
macro_module._request=lambda url,**kw:_Reply(200,'text/csv','',b'observation_date,CPIAUCSL\n2025-01-01,300\n',datetime.now(timezone.utc))
macro_request=ir_search.DataRequest('macro_series',market='GLOBAL',symbols=['FRED.CPIAUCSL'],start='2025-01-01',end='2025-01-31')
assert ir_search.get_data(macro_request).records[0]['value']==300
assert len(ir_search.list_capabilities()['macro_series_catalog']['series'])==11
assert len(ir_search.list_material_capabilities()['company_ir_catalog']['issuers'])==3
for provider in ('hkex','company_ir'):
    preview=ir_search.search_materials(ir_search.MaterialSearchRequest('Tencent',symbols=['00700.HK'],providers=[provider],dry_run=True))
    assert preview.plan['selected_providers']==[provider] and preview.plan['source_calls_started']==0
assert all(s['state']=='configured_unverified' for s in ir_search.diagnose_sources(['global_macro','hkex','company_ir'])['sources'])
if mcp_checked:
    async def check_expanded():
        response=await server.call_tool('get_data',macro_request.to_dict())
        assert json.loads(response[0].text)['records'][0]['value']=='300'
        response=await server.call_tool('search_materials',{'question':'Tencent','symbols':['00700.HK'],'providers':['hkex'],'dry_run':True})
        assert json.loads(response[0].text)['plan']['selected_providers']==['hkex']
    event_loop.run_until_complete(check_expanded())
assert not any(name == 'ir_search.research' or name.startswith('ir_search.research.') for name in sys.modules)
event_loop.close()
print(json.dumps({"installed_only":True, "mcp_checked":mcp_checked}))
'''
    # A file avoids Windows' command-line length limit as this probe grows.
    script_path = unrelated / "installed_package_probe.py"
    script_path.write_text(script, encoding="utf-8")
    result = subprocess.run([sys.executable, str(script_path)], cwd=unrelated, env=clean_env,
                            text=True, encoding="utf-8", capture_output=True, timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["installed_only"]
