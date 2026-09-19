import asyncio
import json
from pathlib import Path
import subprocess
import sys
import types

import pytest

import ir_search
from ir_search import mcp_server


def test_framework_payloads_are_strict_json_and_unavailable_without_adapters():
    payloads = [
        mcp_server.list_capabilities_payload(),
        mcp_server.describe_dataset_payload("prices_daily"),
        mcp_server.get_data_payload({"dataset": "prices_daily", "symbols": ["000001.SZ"],
                                     "start": "2026-09-01", "end": "2026-09-02"}),
        mcp_server.retrieve_payload("revenue", ["file:///private/data"]),
    ]
    for payload in payloads:
        json.dumps(payload, allow_nan=False)
        assert "diagnostics" in payload
    assert payloads[0]["capabilities"] == []
    assert payloads[2]["status"] == "unavailable" and not payloads[2]["records"]
    assert payloads[3]["diagnostics"][0]["code"] == "blocked_url"


@pytest.mark.parametrize("payload_request", [None, [], {"dataset": "prices_daily", "limit": True},
                                        {"dataset": "securities", "password": "must_not_escape"}])
def test_invalid_mcp_data_inputs_do_not_reflect_raw_values(payload_request):
    payload = mcp_server.get_data_payload(payload_request)
    assert payload["status"] == "error" and payload["diagnostics"][0]["code"] == "invalid_request"
    assert "must_not_escape" not in json.dumps(payload)


def test_invalid_retrieval_and_description_are_json_errors():
    assert mcp_server.describe_dataset_payload([])["status"] == "error"
    assert mcp_server.retrieve_payload("question", [])["status"] == "error"
    payload = mcp_server.retrieve_payload("question", ["https://user:must_not_escape@example.com"])
    assert payload["status"] == "error" and "must_not_escape" not in json.dumps(payload)
    assert mcp_server.get_data_payload({"dataset": "securities"}, timeout_seconds=float("inf"))["status"] == "error"


def test_actual_mcp_registration_matches_public_tool_catalog(monkeypatch):
    class FakeFastMCP:
        def __init__(self, *args, **kwargs):
            self.tools = {}

        def tool(self):
            def register(fn):
                self.tools[fn.__name__] = fn
                return fn
            return register

        def run(self):
            pass

    fake_module = types.ModuleType("mcp.server.fastmcp")
    fake_module.FastMCP = FakeFastMCP
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fake_module)
    server = FakeFastMCP()
    monkeypatch.setattr(mcp_server, "make_fastmcp", lambda _: server)
    mcp_server.run()
    assert list(server.tools) == mcp_server.list_tool_names()
    assert set(mcp_server.tool_descriptions()) == set(server.tools)
    assert server.tools["list_capabilities"]()["capabilities"] == []
    assert server.tools["describe_dataset"]("securities")["status"] == "ok"
    assert server.tools["get_data"]("securities")["status"] == "unavailable"
    assert server.tools["retrieve"]("question", ["file:///private/file"])["status"] == "unavailable"
    assert server.tools["search_announcements"](["000001.SZ"], "2026-09-01", "2026-09-03")["status"] == "unavailable"
    forwarded={}
    monkeypatch.setattr(mcp_server,'get_data_payload',lambda payload,**kw:forwarded.update(payload) or {})
    server.tools['get_data']('financial_statements',symbols=['600519.SH'],start='2025-12-31',
        statement='income',statement_scope='parent',period_basis='single_quarter',revision='adjusted')
    assert {k:forwarded[k] for k in ['statement','statement_scope','period_basis','revision']} == {
        'statement':'income','statement_scope':'parent','period_basis':'single_quarter','revision':'adjusted'}


def test_framework_import_does_not_load_legacy_source_clients():
    root = Path(__file__).resolve().parents[1]
    code = """
import sys
import ir_search
import ir_search.mcp_server
assert 'ir_search.kernel' not in sys.modules
assert 'ir_search.adapters.dajiala' not in sys.modules
assert 'tools.gzh_fetch' not in sys.modules
assert not ir_search.list_capabilities()['capabilities']
ir_search.search_materials(ir_search.MaterialSearchRequest('贵州茅台9月动销'))
ir_search.get_data(ir_search.DataRequest('securities'))
ir_search.mcp_server.retrieve_payload('revenue', ['file:///private/blocked'])
assert not any(name == 'ir_search.research' or name.startswith('ir_search.research.') for name in sys.modules)
"""
    subprocess.run([sys.executable, "-c", code], cwd=root, check=True, capture_output=True, text=True)


def test_real_mcp_runtime_registers_and_calls_framework_tools(monkeypatch):
    runtime = pytest.importorskip("mcp.server.fastmcp")
    server = runtime.FastMCP("framework-validation")
    monkeypatch.setattr(server, "run", lambda: None)
    monkeypatch.setattr(mcp_server, "make_fastmcp", lambda _: server)
    mcp_server.run()

    async def check():
        registered = await server.list_tools()
        assert [item.name for item in registered] == mcp_server.list_tool_names()
        schema=next(item.inputSchema for item in registered if item.name=='get_data')
        assert {'statement','statement_scope','period_basis','revision'} <= schema['properties'].keys()
        content = await server.call_tool("get_data", {"dataset": "securities"})
        payload = json.loads(content[0].text)
        assert payload["status"] == "unavailable"
        assert payload["records"] == []
        assert payload["diagnostics"][0]["code"] == "no_data_provider_registered"
        json.dumps(payload, allow_nan=False)

    asyncio.run(check())


def test_legacy_sdk_facades_preserve_arguments(monkeypatch):
    import ir_search.kernel as kernel

    sentinel = object()
    monkeypatch.setattr(kernel, "build_registry", lambda live=None: {"live": live})
    monkeypatch.setattr(kernel, "search", lambda x, **kwargs: (x, kwargs))
    assert ir_search.build_registry(False) == {"live": False}
    assert ir_search.search("question", sentinel, sentinel, sentinel) == (
        "question", {"registry": sentinel, "cache": sentinel, "logger": sentinel})
