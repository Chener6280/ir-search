"""Offline research-query orchestration; fixture adapters are never registered by default."""
import asyncio
import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from ir_search import (
    MaterialSearchRequest, MaterialSearchResult, MaterialSearchPage, MaterialCandidate, MaterialCapability,
    MaterialKind, TextScope, MaterialRegistry, build_material_registry, list_material_capabilities,
    search_materials, list_capabilities, Provenance, AdapterMode, RequestContext, Diagnostic,
)
from ir_search import mcp_server
from ir_search.models import SourceAuthority, SourceTier, EvidenceType, FailureKind
from ir_search.registry import DataAdapterError
from ir_search.adapters.jydb_materials import JYDBMaterialAdapter
from ir_search.infrastructure.credentials import MySQLProfile

NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)


def request(**changes):
    args = dict(question="贵州茅台9月动销", published_start="2026-09-01", published_end="2026-10-10",
                period_start="2026-09-01", period_end="2026-09-30", providers=['source_a'])
    args.update(changes)
    return MaterialSearchRequest(**args)


def candidate(provider="source_a", **changes):
    args = dict(source_ref=f"fixture://{provider}/1", title="贵州茅台渠道反馈", material_type="channel_check",
                channel="community", published_on="2026-10-01", symbols=["600519.SH"],
                text="贵州茅台9月动销出现分化，这是供研究核对的渠道观察。", text_scope="extracted_text",
                provenance=Provenance(provider, "fixture publisher", NOW, authority=SourceAuthority.DATA_VENDOR,
                                      source_tier=SourceTier.UGC, evidence_type=EvidenceType.OPINION, adapter_mode=AdapterMode.LIVE))
    args.update(changes)
    return MaterialCandidate(**args)


class Source:
    def __init__(self, name="source_a", rows=None, **cap):
        self.name = name
        self.capability = MaterialCapability(name, "community", tuple(MaterialKind), **cap)
        self.rows = rows if rows is not None else [candidate(name)]
        self.calls = []
        self.error = None
        self.hook = None
        self.complete = True

    def search_materials(self, request, *, context):
        self.calls.append(request)
        if self.error:
            raise self.error
        if self.hook:
            self.hook(context)
        return MaterialSearchPage(self.rows, len(self.rows), self.complete)


def registry(*sources):
    result = MaterialRegistry()
    for source in sources:
        result.register(source)
    return result


def codes(result):
    return {d.code for d in result.diagnostics} | {gap["code"] for gap in result.gaps}


def test_public_contract_serialization_and_no_implicit_year_or_scan():
    source = Source()
    response = search_materials(MaterialSearchRequest("贵州茅台9月动销"), registry=registry(source))
    assert not source.calls and response.required_inputs == ["published_start", "published_end", "period_start", "period_end", "providers"]
    assert response.plan["symbols"] == ["600519.SH"]
    assert "终端销量" in response.plan["topic_terms"] and "库存" in response.plan["related_terms"]
    assert "research_scope_required" in codes(response)
    json.dumps(response.to_dict(), allow_nan=False)
    assert isinstance(response, MaterialSearchResult)


@pytest.mark.parametrize("changes", [{"question": ""}, {"published_start": "20260901"},
    {"published_end": None}, {"published_end": "2026-08-01"}, {"period_end": None},
    {"period_end": "2026-08-01"}, {"limit": True}, {"limit": 51}, {"max_sources": 0},
    {"candidates_per_source": 51}, {"text_reads_per_source": 11}, {"max_chars": 50001},
    {"providers": ["unsafe?token=secret"]}, {"symbols": ["';DROP"]}, {"symbols": "600519.SH"},
    {"material_types": ["wechat"]}, {"providers": ["jydb"], "exclude_providers": ["jydb"]},
    {"keywords": ["x"] * 2}, {"entities": ["x" * 101]}])
def test_request_rejects_ambiguous_or_unbounded_input(changes):
    with pytest.raises(ValueError):
        request(**changes)


@pytest.mark.parametrize("changes", [{"source_ref": "https://x.test/?token=must_not_escape"},
    {"original_url": "file:///private/source"}, {"published_on": "20260101"},
    {"published_at": "2026-10-02T00:00:00Z"}, {"text_scope": "metadata"},
    {"text": ""}, {"warnings": ["unsafe raw exception!"]}, {"period_start": "2026-09-01"}])
def test_candidate_contract_keeps_references_and_dates_explicit(changes):
    with pytest.raises(ValueError):
        candidate(**changes)


def test_registry_scope_discovery_and_mock_rejection(tmp_path):
    source = Source()
    registered = registry(source)
    assert registered.entries() == (source,) and registered.entries(account_scope="elsewhere") == ()
    with pytest.raises(ValueError):
        registered.register(source)
    with pytest.raises(ValueError):
        registered.register(object())
    with pytest.raises(ValueError):
        MaterialCapability("bad name", "web", ("news",))
    with pytest.raises(ValueError):
        MaterialCapability("source_a", "web", ())
    catalog = list_material_capabilities(registry=registered)
    assert catalog["capabilities"][0]["provider"] == "source_a"
    assert {s["provider"] for s in catalog["unregistered_sources"]} >= {"ima", "zsxq", "wechat", "alphapai"}
    assert list_capabilities(material_registry=registered)["materials"] == catalog
    source.capability = replace(source.capability, adapter_mode="mock")
    result = search_materials(request(), registry=registered)
    assert not source.calls and "non_live_material_source_blocked" in codes(result)
    target = tmp_path / "credentials.env"
    target.write_text("TUSHARE_MCP_TOKEN=unused_private_fixture\n")
    target.chmod(0o600)
    assert not build_material_registry(env_file=target).entries()
    target.write_text("JYDB_MYSQL_ENABLED=true\n")
    assert build_material_registry(env_file=target).diagnostics[0].provider == "jydb"
    assert build_material_registry(env_file=tmp_path / "missing.env").diagnostics


def test_publication_dates_and_business_period_are_independent():
    source = Source(rows=[candidate(period_start="2026-09-01", period_end="2026-09-30")])
    result = search_materials(request(providers=[source.name]), registry=registry(source))
    assert len(result.items) == 1
    version = result.items[0]["versions"][0]
    assert version["published_on"] == "2026-10-01" and version["period_start"] == "2026-09-01"
    assert version["match"]["business_period"] == "overlaps_requested_period"
    assert result.status.value == "partial" and not result.complete
    assert source.calls[0].symbols == ("600519.SH",)
    assert result.plan["selected_providers"] == [source.name]
    source.rows = [candidate()]
    unknown = search_materials(request(providers=[source.name]), registry=registry(source))
    assert "no_evidence_with_verified_business_period" in codes(unknown)
    assert "business_period_not_verified" in unknown.items[0]["versions"][0]["warnings"]


def test_versions_and_citations_are_locatable_and_do_not_become_claims():
    source = Source()
    result = search_materials(request(), registry=registry(source))
    group = result.items[0]
    version = group["versions"][0]
    assert group["independence"] == "not_established"
    assert version["provenance"]["authority"] == "data_vendor"
    assert version["content_hash"] and version["version_id"].startswith("sha256:")
    for span in version["evidence_spans"]:
        assert span["text"] == version[span["source_part"]][span["start_char"]:span["end_char"]]
        assert span["version_id"] == version["version_id"] and span["url"] is None
    assert result.source_text_trust == "untrusted" and "interpretation" in version["match"]
    changed = Source(rows=[candidate(text="贵州茅台9月动销：新的渠道反馈。")])
    newer = search_materials(request(), registry=registry(changed)).items[0]["versions"][0]
    assert newer["version_id"] != version["version_id"]


def test_proxy_only_abstract_metadata_and_unknown_publication_are_disclosed():
    rows = [candidate(text="贵州茅台批价变化，库存增加。", text_scope="abstract", published_on=None),
            candidate(source_ref="fixture://source_a/2", title="贵州茅台库存", text="", text_scope="metadata")]
    result = search_materials(request(providers=["source_a"]), registry=registry(Source(rows=rows)))
    assert "related_terms_only_not_direct_topic_evidence" in codes(result)
    warnings = {w for g in result.items for v in g["versions"] for w in v["warnings"]}
    assert {"abstract_not_full_text", "metadata_only", "publication_date_unknown"} <= warnings
    assert all(v["match"]["classification"] == "related_term_match" for g in result.items for v in g["versions"])


def test_same_original_url_keeps_all_versions_and_retrieval_sources():
    url = "https://example.test/original.pdf"
    a = Source("source_a", [candidate("source_a", original_url=url)])
    b = Source("source_b", [candidate("source_b", original_url=url, text="贵州茅台9月动销：不同版本。")])
    result = search_materials(request(providers=["source_a", "source_b"]), registry=registry(a, b))
    assert len(result.items) == 1
    group = result.items[0]
    assert group["duplicate_group"] and len(group["versions"]) == 2
    assert group["retrieval_providers"] == ["source_a", "source_b"]
    assert len({v["version_id"] for v in group["versions"]}) == 2


def test_exact_reposts_across_locations_merge_but_similar_titles_do_not():
    text = "贵州茅台9月动销渠道观察。" + "正文中的样本口径需由研究者核对。" * 12
    a = Source("source_a", [candidate("source_a", original_url="https://a.test/post", text=text)])
    b = Source("source_b", [candidate("source_b", original_url="https://b.test/post", text=text)])
    r = search_materials(request(providers=["source_a", "source_b"]), registry=registry(a, b))
    assert len(r.items) == 1 and r.items[0]["grouping_basis"] == "identical_text_across_locations"
    b.rows = [replace(b.rows[0], text=text + "后续修正")]
    assert len(search_materials(request(providers=["source_a", "source_b"]), registry=registry(a, b)).items) == 2
    b.rows = [replace(b.rows[0], text=text, warnings=("text_truncated",))]
    assert len(search_materials(request(providers=["source_a", "source_b"]), registry=registry(a, b)).items) == 2


def test_source_budgets_filters_and_unregistered_channels_are_visible():
    a, b = Source("source_a"), Source("source_b")
    result = search_materials(request(providers=["source_b", "source_a", "ima"], max_sources=1), registry=registry(a, b))
    assert b.calls and not a.calls
    assert {"source_budget_exhausted", "material_source_not_registered"} <= codes(result)
    a.calls.clear()
    r = search_materials(request(providers=[], exclude_providers=["source_a"], material_types=["call_transcript"]), registry=registry(a))
    assert not a.calls and not r.items
    one = Source("source_a", requires_symbols=True, max_symbols=1)
    r = search_materials(request(symbols=["600519.SH", "000001.SZ"]), registry=registry(one))
    assert "source_symbol_limit" in codes(r) and not one.calls


def test_identical_prefixes_without_urls_do_not_merge_truncated_or_different_scopes():
    text = "贵州茅台动销情况。" * 30
    a = Source("source_a", [candidate("source_a", text=text)])
    b = Source("source_b", [candidate("source_b", text=text)])
    args = dict(providers=["source_a", "source_b"])
    assert len(search_materials(request(**args), registry=registry(a, b)).items) == 1
    assert len(search_materials(request(**args, max_chars=120), registry=registry(a, b)).items) == 2
    b.rows = [replace(b.rows[0], text_scope="abstract")]
    assert len(search_materials(request(**args), registry=registry(a, b)).items) == 2


def test_entity_resolution_from_explicit_entities_and_generic_keywords():
    source = Source()
    r = search_materials(request(question="9月动销", entities=["贵州茅台"]), registry=registry(source))
    assert r.plan["symbols"] == ["600519.SH"] and r.items
    source.rows = [candidate(text="贵州茅台收入情况。")]
    r = search_materials(request(question="贵州茅台收入情况", period_start=None, period_end=None), registry=registry(source))
    assert r.items and "收入" in r.plan["topic_terms"]


def test_outside_publication_or_business_period_and_unrelated_entities_excluded():
    rows = [candidate(published_on="2025-01-01"),
            candidate(source_ref="fixture://source_a/2", period_start="2025-09-01", period_end="2025-09-30"),
            candidate(source_ref="fixture://source_a/3", title="其他公司", symbols=["000001.SZ"], text="其他公司动销增加。")]
    r = search_materials(request(providers=["source_a"]), registry=registry(Source(rows=rows)))
    assert not r.items and "candidate_outside_publication_window" in codes(r)
    assert "no_match_in_scanned_records" in codes(r)


@pytest.mark.parametrize("error", [DataAdapterError("entitlement_denied"), RuntimeError("private-key-must-not-escape")])
def test_one_source_failure_keeps_other_evidence_and_sanitizes_errors(error):
    a, b = Source("source_a"), Source("source_b")
    a.error = error
    r = search_materials(request(providers=["source_a", "source_b"]), registry=registry(a, b))
    assert len(r.items) == 1 and r.items[0]["retrieval_providers"] == ["source_b"]
    assert r.status.value == "partial" and "private-key-must-not-escape" not in json.dumps(r.to_dict())


def test_context_cancellation_and_deadlines_stop_later_sources_and_reject_late_pages():
    a, b = Source("source_a"), Source("source_b")
    context = RequestContext()
    context.cancel()
    result = search_materials(request(providers=["source_a", "source_b"]), registry=registry(a, b), context=context)
    assert not a.calls and not b.calls and "cancelled" in codes(result)
    a.hook = lambda context: context.cancel()
    result = search_materials(request(providers=["source_a", "source_b"]), registry=registry(a, b))
    assert not result.items and not b.calls and "cancelled" in codes(result)
    a.hook = None
    result = search_materials(request(providers=["source_a", "source_b"]), registry=registry(a, b), context=RequestContext(max_operations=1))
    assert result.items and "operation_budget_exhausted" in codes(result) and not b.calls


def test_invalid_pages_and_provenance_are_not_authoritative():
    bad = Source(rows=[candidate(provenance=Provenance("wrong", "unknown", NOW, adapter_mode="live"))])
    assert not search_materials(request(), registry=registry(bad)).items
    bad.rows = [candidate(), candidate()]
    assert "invalid_material_response" in codes(search_materials(request(), registry=registry(bad)))
    bad.rows = [candidate(provenance=Provenance("source_a", "unknown", NOW, adapter_mode="mock"))]
    assert not search_materials(request(), registry=registry(bad)).items
    with pytest.raises(ValueError):
        search_materials("invalid request")


def test_truncation_and_result_limit_preserve_diagnostics():
    rows = [candidate(text="贵州茅台动销" + "很多内容" * 100), candidate(source_ref="fixture://source_a/2", text="贵州茅台终端销售。")]
    r = search_materials(request(limit=1, max_chars=50), registry=registry(Source(rows=rows)))
    assert len(r.items) == 1 and "result_limit_reached" in codes(r)
    r = search_materials(request(max_chars=50), registry=registry(Source(rows=rows)))
    assert any("text_truncated" in v["warnings"] for g in r.items for v in g["versions"])


def test_returned_evidence_distinguishes_titles_abstracts_text_and_unknown_dates():
    rows = [candidate(title="贵州茅台动销", text="", text_scope="metadata"),
            candidate(source_ref="fixture://source_a/2", text="贵州茅台动销观察。", text_scope="abstract"),
            candidate(source_ref="fixture://source_a/3", text="贵州茅台动销观察。" * 30,
                      published_on=None, period_start="2026-09-01", period_end="2026-09-30")]
    result = search_materials(request(providers=["source_a", "ima"], max_chars=80), registry=registry(Source(rows=rows)))
    state, missing = result.coverage
    audit = state["returned_evidence"]
    assert state["scanned_count"] == state["matched_count"] == 3
    assert audit["scope"] == "returned_items_after_limit"
    assert audit["document_groups"] == audit["source_records"] == 3
    assert audit["text_scope_counts"] == {"metadata": 1, "abstract": 1, "extracted_text": 1, "search_snippet": 0, "source_excerpt": 0}
    assert audit["citation_scope_counts"] == {"metadata": 1, "abstract": 1, "extracted_text": 1, "search_snippet": 0, "source_excerpt": 0}
    assert audit["truncated_records"] == audit["publication_date_unknown_records"] == 1
    assert audit["business_period_unverified_records"] == 2
    assert missing["state"] == "not_registered" and missing["returned_evidence"]["source_records"] == 0
    assert not any(missing["returned_evidence"]["citation_scope_counts"].values())
    assert result.status.value == "partial" and not result.complete


def test_returned_evidence_counts_after_limit_and_preserves_duplicate_source_records():
    url = "https://example.test/disclosure"
    a = Source(rows=[candidate(original_url=url),
                     candidate(source_ref="fixture://source_a/2", original_url=url),
                     candidate(source_ref="fixture://source_a/3", title="贵州茅台库存", text="", text_scope="metadata")])
    b = Source("source_b", [candidate("source_b", original_url=url)])
    result = search_materials(request(providers=["source_a", "source_b"], limit=1), registry=registry(a, b))
    first, second = result.coverage
    assert first["scanned_count"] == first["matched_count"] == 3
    assert first["returned_evidence"]["document_groups"] == second["returned_evidence"]["document_groups"] == 1
    assert first["returned_evidence"]["source_records"] == 2
    assert second["returned_evidence"]["source_records"] == 1
    assert first["returned_evidence"]["text_scope_counts"]["metadata"] == 0
    assert len(result.items) == 1 and result.items[0]["independence"] == "not_established"
    assert "result_limit_reached" in codes(result)


def test_failed_source_evidence_audit_is_empty_while_successful_source_survives():
    a, b = Source(), Source("source_b")
    a.error = DataAdapterError("network")
    result = search_materials(request(providers=["source_a", "source_b"]), registry=registry(a, b))
    assert result.coverage[0]["state"] == "network"
    assert result.coverage[0]["returned_evidence"]["source_records"] == 0
    assert result.coverage[1]["returned_evidence"]["text_scope_counts"]["extracted_text"] == 1
    assert result.items and result.status.value == "partial"


def test_jydb_bridge_uses_existing_client_and_does_not_invent_period_or_url():
    from test_jydb_adapter import Database, PROFILE, listing
    database = Database()
    database.listings = [dict(listing(), InfoTitle="终端销售说明")]
    adapter = JYDBMaterialAdapter(PROFILE, client=database.adapter())
    req = MaterialSearchRequest("销售说明", symbols=["000001.SZ"], keywords=["销售"], providers=['jydb'],
                               published_start="2026-09-01", published_end="2026-09-03")
    result = search_materials(req, registry=registry(adapter))
    assert result.items
    row = result.items[0]["versions"][0]
    assert row["original_url"] is None and row["period_start"] is None and row["published_at"] is None
    assert row["source_ref"].startswith("jydb://announcement/") and row["provenance"]["authority"] == "data_vendor"
    assert all(d.adapter_mode == AdapterMode.LIVE for d in result.diagnostics if d.provider == "jydb")
    assert any("LEFT(Content" in sql for sql, _ in database.calls)
    no_text = search_materials(replace(req, text_reads_per_source=0), registry=registry(adapter))
    assert no_text.items[0]["versions"][0]["text_scope"] == "metadata"
    assert "text_read_budget_exhausted" in codes(no_text)
    adapter._client.fetch_document = lambda *args, **kwargs: (_ for _ in ()).throw(DataAdapterError("entitlement_denied"))
    failed_text = search_materials(req, registry=registry(adapter))
    assert failed_text.items and "entitlement_denied" in codes(failed_text)
    with pytest.raises(ValueError):
        JYDBMaterialAdapter(MySQLProfile("wind_mysql", "host", "db", "user", "fixture"))


def test_configured_jydb_bridge_and_mcp_payload(tmp_path):
    target = tmp_path / "credentials.env"
    target.write_text("JYDB_MYSQL_ENABLED=true\nJYDB_MYSQL_HOST=host\nJYDB_MYSQL_DATABASE=db\nJYDB_MYSQL_USER=user\nJYDB_MYSQL_PASSWORD=fixture_private\n")
    target.chmod(0o600)
    built = build_material_registry(env_file=target)
    assert built.entries()[0].name == "jydb"
    assert "fixture_private" not in json.dumps(list_material_capabilities(registry=built))
    result = mcp_server.search_materials_payload(request().to_dict(), registry=registry(Source()))
    assert result["items"] and result["status"] == "partial"
    for invalid in [None, [], {"question":"x", "token":"must_not_escape"}, {"question":"x", "limit":True}]:
        payload = mcp_server.search_materials_payload(invalid)
        assert payload["status"] == "error" and "must_not_escape" not in json.dumps(payload)


def test_real_mcp_registration_and_call(monkeypatch):
    runtime = pytest.importorskip("mcp.server.fastmcp")
    server = runtime.FastMCP("material-search-offline-test")
    monkeypatch.setattr(server, "run", lambda: None)
    monkeypatch.setattr(mcp_server, "make_fastmcp", lambda _: server)
    original = mcp_server.search_materials_payload
    monkeypatch.setattr(mcp_server, "search_materials_payload", lambda args, **kwargs: original(args, registry=registry(Source()), **kwargs))
    mcp_server.run()
    async def check():
        tools = await server.list_tools()
        assert len(tools) == 12
        legacy = next(tool for tool in tools if tool.name == "deep_research")
        assert "compatibility-only" in legacy.description.lower() and "search_materials" in legacy.description
        content = await server.call_tool("search_materials", request().to_dict())
        response = json.loads(content[0].text)
        assert response["items"] and response["source_text_trust"] == "untrusted"
        summary = next(s["returned_evidence"] for s in response["coverage"] if s["provider"] == "source_a")
        assert summary["text_scope_counts"]["extracted_text"] == 1
    asyncio.run(check())
