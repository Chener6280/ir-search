"""Synthetic corpus responses only; private subscription data is never a fixture."""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import json

import pytest

from ir_search import MaterialSearchRequest, MaterialSourceScan, MaterialRegistry, search_materials, build_material_registry
from ir_search import mcp_server
from ir_search.adapters.tushare_corpus import TushareCorpusAdapter
from ir_search.context import RequestContext, RequestStopped
from ir_search.infrastructure.credentials import TushareCorpusProfile, tushare_corpus_profile, SourceConfigError, source_configuration_status
from ir_search.infrastructure.tushare_corpus import TushareCorpusClient, CorpusResponse, _RPCResponse, FIELDS
from ir_search.registry import DataAdapterError

PROFILE = TushareCorpusProfile("synthetic_corpus_key")
NOW = datetime(2026, 9, 15, tzinfo=timezone.utc)


def row(tool):
    common = {"title": "贵州茅台经营观察", "url": "https://example.test/" + tool}
    if tool == "research_report":
        return dict(common, trade_date="20260914", report_code="R123", ts_code="600519.SH", author="甲,乙",
                    inst_csname="测试研究所", abstr="贵州茅台动销改善，但样本覆盖有限。")
    if tool == "npr":
        return dict(common, pubtime="2026-09-14 12:00:00", pcode="测试〔2026〕1号", puborg="测试部门",
                    content_html="<p>贵州茅台动销政策素材。</p><script>secret_script</script><p>应核实政策口径。</p>")
    return dict(common, pub_time="2026-09-14 12:00:00", src="新浪财经", content="<h1>贵州茅台动销新闻</h1><p>终端销售变化。</p>")


def request(**kw):
    args = dict(question="贵州茅台动销", symbols=["600519.SH"], published_start="2026-09-01", published_end="2026-09-14", providers=["tushare_corpus"])
    args.update(kw)
    return MaterialSearchRequest(**args)


class Client:
    def __init__(self):
        self.calls = []
        self.rows = {name: [row(name)] for name in FIELDS}
        self.errors = {}
    def fetch(self, tool, arguments, *, context):
        self.calls.append((tool, arguments))
        if tool in self.errors:
            raise self.errors[tool]
        return CorpusResponse(self.rows[tool], NOW)


def setup(client=None, profile=PROFILE):
    client = client or Client()
    reg = MaterialRegistry()
    reg.register(TushareCorpusAdapter(profile, client_factory=lambda _: client))
    return reg, client


def test_three_material_types_dates_authors_provenance_and_locatable_citations():
    reg, client = setup()
    result = search_materials(request(), registry=reg)
    assert len(result.items) == 3 and result.status.value == "partial" and not result.complete
    versions = {g["material_type"]: g["versions"][0] for g in result.items}
    report = versions["research_report"]
    assert report["text_scope"] == "abstract" and report["authors"] == ["甲", "乙"]
    assert report["source_document_id"] == "R123" and report["published_at"] is None
    assert report["provenance"]["evidence_type"] == "broker_report"
    assert versions["policy"]["published_at"].endswith("+08:00")
    assert versions["policy"]["source_document_id"] == "测试〔2026〕1号"
    for version in versions.values():
        assert version["period_start"] is None and version["provenance"]["authority"] == "data_vendor"
        assert "<p>" not in version["text"] and "secret_script" not in version["text"]
        for span in version["evidence_spans"]:
            assert span["text"] == version[span["source_part"]][span["start_char"]:span["end_char"]]
    calls = dict(client.calls)
    assert calls["research_report"]["start_date"] == "20260901"
    assert calls["npr"]["start_date"] == "2026-09-14 00:00:00"
    assert calls["major_news"]["end_date"] == "2026-09-14 23:59:59"
    assert calls["major_news"]["src"] == "新浪财经"
    scans = result.coverage[0]["scans"]
    assert len(scans) == 3 and sum(s["inspected_count"] for s in scans) == 3
    assert scans[1]["query_start"] == "2026-09-14"
    json.dumps(result.to_dict(), allow_nan=False)


def test_budgets_bound_inspection_and_body_parsing_without_claiming_remote_limit():
    client = Client()
    client.rows = {name: [dict(row(name), url="https://example.test/%s/%s" % (name,i)) for i in range(5)] for name in FIELDS}
    reg, client = setup(client)
    result = search_materials(request(candidates_per_source=6, text_reads_per_source=1, max_chars=8), registry=reg)
    coverage = result.coverage[0]
    assert coverage["scanned_count"] == 6
    assert sum(s["received_count"] for s in coverage["scans"]) == 15
    assert all("limit" not in args for _,args in client.calls)
    kinds = coverage["returned_evidence"]["text_scope_counts"]
    # Titles do not contain the research term; unread metadata is not falsely matched.
    assert kinds["abstract"] == 2 and kinds["extracted_text"] == 1 and kinds["metadata"] == 0
    assert {"text_read_budget_exhausted","corpus_candidate_budget_exhausted"} <= {d.code for d in result.diagnostics}
    reg, limited = setup(profile=replace(PROFILE, max_calls_per_query=1))
    result = search_materials(request(), registry=reg)
    assert len(limited.calls) == 1 and len(result.coverage[0]["scans"]) == 3
    assert result.coverage[0]["scans"][1]["state"] == "not_queried_budget"


def test_report_window_and_unscoped_report_scan_are_explicitly_restricted():
    reg, client = setup()
    search_materials(request(published_start="2025-09-15", material_types=["research_report"]), registry=reg)
    assert client.calls[0][1]["start_date"] == "20260815"
    reg, client = setup()
    result = search_materials(request(question="行业动销", symbols=[], material_types=["research_report"]), registry=reg)
    assert client.calls[0][1]["start_date"] == "20260914" and "ts_code" not in client.calls[0][1]
    assert result.coverage[0]["scans"][0]["symbols"] == []


def test_one_type_failure_keeps_other_types_and_all_failures_are_unavailable():
    reg, client = setup()
    client.errors["npr"] = DataAdapterError("entitlement_denied")
    result = search_materials(request(), registry=reg)
    assert len(result.items) == 2
    assert result.coverage[0]["scans"][-1]["state"] == "entitlement_denied"
    client.errors = {tool: DataAdapterError("rate_limit") for tool in FIELDS}
    result = search_materials(request(), registry=reg)
    assert not result.items and result.status.value == "unavailable"
    assert result.coverage[0]["state"] == "rate_limit"


@pytest.mark.parametrize("change", [dict(trade_date="2026-09-14"),dict(ts_code="000001.SZ"), dict(url="https://x.test/?token=private"),dict(title=None),dict(abstr={"wrong":"shape"}),dict(trade_date="20260930")])
def test_invalid_rows_are_skipped_with_schema_diagnostics(change):
    reg, client = setup()
    client.rows["research_report"] = [dict(row("research_report"), **change)]
    result = search_materials(request(material_types=["research_report"]), registry=reg)
    assert not result.items and "invalid_corpus_record" in {d.code for d in result.diagnostics}


def test_missing_text_empty_windows_duplicate_versions_and_cancellation():
    reg, client = setup()
    value = dict(row("research_report"), title="贵州茅台动销", abstr=None, url=None)
    client.rows["research_report"] = [value, value, dict(value, abstr="贵州茅台动销不同版本。")]
    result = search_materials(request(material_types=["research_report"]), registry=reg)
    assert len(result.items) == 2 and "duplicate_corpus_record" in {d.code for d in result.diagnostics}
    assert any(v["text_scope"] == "metadata" for g in result.items for v in g["versions"])
    client.rows["research_report"] = []
    result = search_materials(request(material_types=["research_report"]), registry=reg)
    assert not result.items and "corpus_empty_window" in {d.code for d in result.diagnostics}
    context = RequestContext(); context.cancel(); client.calls.clear()
    search_materials(request(), registry=reg, context=context)
    assert not client.calls


def test_config_is_private_opt_in_and_never_uses_legacy_token(tmp_path):
    assert tushare_corpus_profile(values={"TUSHARE_MCP_TOKEN":PROFILE.token}) is None
    with pytest.raises(SourceConfigError):
        tushare_corpus_profile(values={"TUSHARE_CORPUS_ENABLED":"true", "TUSHARE_TOKEN":PROFILE.token})
    assert PROFILE.token not in repr(PROFILE)
    path = tmp_path / "credentials.env"
    path.write_text("TUSHARE_CORPUS_ENABLED=true\nTUSHARE_MCP_TOKEN="+PROFILE.token+"\n"); path.chmod(0o600)
    reg = build_material_registry(env_file=path)
    assert [a.name for a in reg.entries()] == ["tushare_corpus"]
    status = source_configuration_status(env_file=path)
    assert PROFILE.token not in json.dumps(status)
    assert next(s for s in status["sources"] if s["provider"]=="tushare_corpus")["live_verified"] is False
    for changes in [dict(token=""),dict(max_calls_per_query=True),dict(news_source="unverified"),dict(token="https://x/?token=private")]:
        with pytest.raises(SourceConfigError): replace(PROFILE, **changes)
    with pytest.raises(ValueError): TushareCorpusAdapter(None)
    with pytest.raises(ValueError): MaterialSourceScan("npr","policy","2026-09-14","2026-09-14","queried",1,2)


class Transport:
    def __init__(self):
        self.calls=[]; self.result={"content":[{"type":"text","text":"[]"}],"isError":False}
    def __call__(self, profile, payload, **kwargs):
        self.calls.append((payload,kwargs))
        if payload["method"]=="notifications/initialized": return _RPCResponse({})
        result = {"protocolVersion":"2025-03-26"} if payload["method"]=="initialize" else self.result
        return _RPCResponse({"jsonrpc":"2.0","id":payload["id"],"result":result},"offline-session")


def arguments():
    return {"start_date":"20260901","end_date":"20260914","fields":list(FIELDS["research_report"]),"ts_code":"600519.SH"}


def test_client_protocol_initializes_once_and_limits_whitelisted_calls():
    transport=Transport(); client=TushareCorpusClient(PROFILE,transport=transport); context=RequestContext(max_operations=20)
    for _ in range(3):
        result=client.fetch("research_report",arguments(),context=context)
        assert result.rows==[] and result.fetched_at.utcoffset().total_seconds()==0
    assert client.http_requests==5 and client.tool_calls==3 and context.operations==5
    assert transport.calls[2][1]["session_id"]=="offline-session"
    with pytest.raises(DataAdapterError,match="tushare_call_budget_exhausted"):
        client.fetch("research_report",arguments(),context=context)
    assert len(transport.calls)==5


@pytest.mark.parametrize("tool,changes", [("p_delete",{}),("research_report",{"token":"private"}),("research_report",{"fields":["token"]}),
    ("research_report",{"start_date":"2026-09-01"}),("research_report",{"end_date":"20270101"}),("major_news",{})])
def test_client_rejects_unsafe_or_unbounded_calls_before_network(tool,changes):
    transport=Transport(); client=TushareCorpusClient(PROFILE,transport=transport)
    with pytest.raises(DataAdapterError): client.fetch(tool,dict(arguments(),**changes),context=RequestContext())
    assert not transport.calls


@pytest.mark.parametrize("result,code", [({"content":[{"type":"text","text":"没有权限"}],"isError":True},"entitlement_denied"),
    ({"content":[{"type":"text","text":"{broken"}]},"upstream_schema"),
    ({"content":[{"type":"image","data":"secret"}]},"upstream_schema"),
    ({"content":[{"type":"text","text":json.dumps([{"title":PROFILE.token}])}]},"upstream_schema"),
    ({"content":[{"type":"text","text":"[1]"}]},"upstream_schema")])
def test_client_tool_failures_and_secret_echo_are_sanitized(result,code):
    transport=Transport(); transport.result=result; client=TushareCorpusClient(PROFILE,transport=transport)
    with pytest.raises(DataAdapterError,match=code) as caught:
        client.fetch("research_report",arguments(),context=RequestContext())
    assert PROFILE.token not in str(caught.value)


@pytest.mark.parametrize("count", [401, 629, 1000])
def test_news_can_exceed_documented_count_with_explicit_local_scan_budget(count):
    transport = Transport()
    transport.result = {"content": [{"type": "text", "text": json.dumps([
        dict(row("major_news"), title="贵州茅台动销观察", url="https://example.test/news/"+str(i)) for i in range(count)
    ])}], "isError": False}
    reg = MaterialRegistry()
    reg.register(TushareCorpusAdapter(PROFILE, client_factory=lambda profile: TushareCorpusClient(profile, transport=transport)))
    result = search_materials(request(material_types=["news"], candidates_per_source=5, text_reads_per_source=3), registry=reg)
    scan = result.coverage[0]["scans"][0]
    assert scan["received_count"] == count and scan["inspected_count"] == 5
    assert result.coverage[0]["returned_evidence"]["text_scope_counts"] == {"metadata": 2, "abstract": 0, "extracted_text": 3, "search_snippet": 0, "source_excerpt": 0}
    assert "corpus_documented_row_limit_exceeded" in {d.code for d in result.diagnostics}
    assert not result.complete


def test_client_keeps_a_hard_row_limit_independent_of_vendor_documentation():
    transport = Transport()
    transport.result = {"content": [{"type": "text", "text": json.dumps([row("research_report")] * 1001)}]}
    client = TushareCorpusClient(PROFILE, transport=transport)
    with pytest.raises(DataAdapterError, match="tushare_response_too_many_rows"):
        client.fetch("research_report", arguments(), context=RequestContext())


def test_sdk_and_real_mcp_share_corpus_output(monkeypatch):
    reg, _ = setup()
    sdk=search_materials(request(),registry=reg).to_dict()
    runtime=pytest.importorskip("mcp.server.fastmcp")
    server=runtime.FastMCP("corpus-offline"); monkeypatch.setattr(server,"run",lambda:None)
    monkeypatch.setattr(mcp_server,"make_fastmcp",lambda _:server)
    original=mcp_server.search_materials_payload
    monkeypatch.setattr(mcp_server,"search_materials_payload",lambda args,**kw:original(args,registry=reg,**kw))
    mcp_server.run()
    async def check():
        response=await server.call_tool("search_materials",request().to_dict())
        result=json.loads(response[0].text)
        assert result["items"]==sdk["items"] and result["coverage"]==sdk["coverage"]
    asyncio.run(check())
