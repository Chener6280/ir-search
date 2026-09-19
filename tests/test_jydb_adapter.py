from dataclasses import replace
from datetime import datetime
import json

import pytest

from ir_search import AnnouncementRequest, MaterialRequest, RequestContext, retrieve, search_announcements
from ir_search.adapters.jydb import JYDBAnnouncementAdapter, build_jydb_adapter
from ir_search.infrastructure.credentials import MySQLProfile
from ir_search.mcp_server import search_announcements_payload
from ir_search.registry import DataAdapterError

PROFILE = MySQLProfile("jydb", "host", "db", "user", "must_not_escape")


def request(**kw):
    return AnnouncementRequest(["000001.SZ"], "2026-09-01", "2026-09-03", **kw)


def listing(identity=101, day=2):
    return dict(ID=identity, CompanyCode=7, InfoPublDate=datetime(2026,9,day), InfoTitle="半年报",
                Category=6, Media="fixture disclosure channel")


class Database:
    def __init__(self):
        self.calls = []
        self.securities = [{"InnerCode":1, "SecuCode":"000001", "SecuMarket":90, "CompanyCode":7}]
        self.listings = [listing()]
        self.documents = [dict(listing(), Content="公司收入增长。\n\n现金流改善。")]
        self.publishers = [{"ChiName":"fixture issuer"}]

    def select(self, profile, sql, params, **kw):
        self.calls.append((sql,params))
        if "DISTINCT ChiName" in sql:
            return self.publishers
        if "FROM SecuMain" in sql:
            return self.securities
        if "LEFT(Content" in sql:
            return self.documents
        return self.listings

    def adapter(self):
        return JYDBAnnouncementAdapter(PROFILE, select=self.select)


def test_jydb_maps_listing_to_company_with_separate_single_table_queries():
    db = Database()
    result = search_announcements(request(query="年报"), adapter=db.adapter())
    assert result["status"] == "ok" and result["complete"]
    assert result["items"][0]["source_ref"] == "jydb://announcement/101"
    assert result["items"][0]["published_at"] == "2026-09-02T00:00:00+08:00"
    assert result["coverage"]["all_disclosures"] is False
    assert all("JOIN" not in sql for sql,_ in db.calls)
    assert db.calls[0][1] == ("000001", 90, 201)
    assert 7 in db.calls[1][1] and "%年报%" in db.calls[1][1]
    assert "must_not_escape" not in json.dumps(result)


def test_jydb_title_phrase_is_parameterized_and_like_wildcards_are_literal():
    db = Database()
    query = "100%_! ' OR 1=1"
    search_announcements(request(query=query), adapter=db.adapter())
    assert query not in db.calls[-1][0]
    assert "%100!%!_!! ' OR 1=1%" in db.calls[-1][1]


def test_jydb_cursor_is_bound_and_uses_date_plus_id_for_same_day_rows():
    db = Database()
    db.listings = [listing(102), listing(101)]
    first = search_announcements(request(limit=1), adapter=db.adapter())
    assert not first["complete"] and first["next_cursor"]
    second_request = request(limit=1, cursor=first["next_cursor"])
    db.listings = [listing(101)]
    second = search_announcements(second_request, adapter=db.adapter())
    assert second["complete"]
    assert db.calls[-1][1][-3:] == (datetime(2026,9,2), 102, 2)
    db.calls.clear()
    changed = search_announcements(replace(second_request, query="changed"), adapter=db.adapter())
    assert changed["diagnostics"][0]["code"] == "invalid_cursor" and not db.calls


def test_jydb_missing_company_does_not_silently_drop_requested_symbol():
    db = Database()
    result = search_announcements(replace(request(), symbols=["000001.SZ", "600519.SH"]), adapter=db.adapter())
    assert not result["complete"] and not result["items"] and result["diagnostics"][0]["code"] == "not_found"
    assert len(db.calls) == 1


def test_jydb_fetch_and_retrieve_preserve_text_citations_and_vendor_boundary(monkeypatch):
    db = Database()
    monkeypatch.setattr("ir_search.adapters.jydb.build_jydb_adapter", lambda: db.adapter())
    monkeypatch.setattr("ir_search.services.retrieval.read_web_document", lambda *a, **kw: pytest.fail("No HTTP for provider reference"))
    bundle = retrieve(MaterialRequest("收入", ["jydb://announcement/101"]))
    assert bundle.status.value == "partial" and len(bundle.materials) == 1
    material = bundle.materials[0]
    assert material.text.startswith("公司收入增长") and material.evidence_spans
    assert material.provenance.publisher == "fixture issuer"
    assert material.provenance.authority.value == "data_vendor"
    assert "vendor_text_not_original_file" in material.warnings and "source_uri_not_web_url" in material.warnings
    assert material.evidence_spans[0]["url"] == "jydb://announcement/101"
    assert "must_not_escape" not in json.dumps(bundle.to_dict(), ensure_ascii=False)


def test_jydb_truncated_text_and_unknown_publisher_are_explicit():
    db = Database()
    db.publishers = []
    doc = db.adapter().fetch_document("jydb://announcement/101", context=RequestContext(), max_chars=5)
    assert len(doc.text) == 5 and "text_truncated" in doc.warnings
    assert doc.extra["publisher"] == "unknown"
    assert db.calls[0][1] == (6,101,2)


@pytest.mark.parametrize("uri", ["jydb://announcement/0", "jydb://announcement/101?token=secret", "jydb://announcement/1;DELETE", "https://example.com", "jydb://announcement/9999999999999999999"])
def test_jydb_rejects_invalid_provider_references_without_query(uri):
    db = Database()
    with pytest.raises(DataAdapterError):
        db.adapter().fetch_document(uri, context=RequestContext())
    assert not db.calls


def test_jydb_disabled_configuration_and_public_input_validation(tmp_path, monkeypatch):
    path = tmp_path / "credentials.env"
    path.write_text("JYDB_MYSQL_ENABLED=false\n")
    path.chmod(0o600)
    monkeypatch.setenv("IR_SEARCH_CREDENTIALS_FILE", str(path))
    with pytest.raises(DataAdapterError, match="source_disabled"):
        build_jydb_adapter()
    assert search_announcements(request())["diagnostics"][0]["code"] == "source_disabled"
    assert search_announcements_payload(["bad-secret"], "2026-09-01", "2026-09-03")["diagnostics"][0]["code"] == "invalid_request"
    assert retrieve(MaterialRequest("question", ["jydb://announcement/101"])).diagnostics[0].code == "source_disabled"
    path.write_text("JYDB_MYSQL_ENABLED=true\n")
    with pytest.raises(DataAdapterError, match="source_config_error"):
        build_jydb_adapter()
    path.write_text("JYDB_MYSQL_ENABLED=true\nJYDB_MYSQL_HOST=host\nJYDB_MYSQL_DATABASE=db\nJYDB_MYSQL_USER=user\nJYDB_MYSQL_PASSWORD=must_not_escape\n")
    assert isinstance(build_jydb_adapter(), JYDBAnnouncementAdapter)
    with pytest.raises(ValueError):
        search_announcements("question")


@pytest.mark.parametrize("changes", [{"symbols": []}, {"symbols": ["000001.SZ"] * 2}, {"start": "20260901"},
                                      {"end": "2026-08-01"}, {"limit": True}, {"limit": 201}, {"query": None}, {"cursor": ""}])
def test_announcement_contract_rejects_ambiguous_inputs(changes):
    with pytest.raises(ValueError):
        replace(request(), **changes)


def test_bad_source_rows_and_cancelled_requests_are_never_successful():
    db = Database()
    db.listings = [dict(listing(), CompanyCode=999)]
    result = search_announcements(request(), adapter=db.adapter())
    assert not result["items"] and result["diagnostics"][0]["code"] == "upstream_schema"
    context = RequestContext()
    context.cancel()
    db.calls.clear()
    result = search_announcements(request(), adapter=db.adapter(), context=context)
    assert result["diagnostics"][0]["code"] == "cancelled" and not db.calls


def test_partial_fetch_failure_preserves_prior_material_without_raw_error(monkeypatch):
    db = Database()
    adapter = db.adapter()
    original = adapter.fetch_document
    def fetch(uri, **kw):
        if uri.endswith("102"):
            raise DataAdapterError("entitlement_denied")
        return original(uri, **kw)
    adapter.fetch_document = fetch
    monkeypatch.setattr("ir_search.adapters.jydb.build_jydb_adapter", lambda: adapter)
    result = retrieve(MaterialRequest("收入", ["jydb://announcement/101", "jydb://announcement/102"]))
    assert len(result.materials) == 1 and result.status.value == "partial"
    assert result.diagnostics[-1].code == "entitlement_denied"


def test_default_retrieve_budget_handles_ten_database_documents_but_respects_caller_budget(monkeypatch):
    db = Database()
    original_select = db.select
    def select(profile, sql, params, **kwargs):
        kwargs["context"].begin_operation()
        if "LEFT(Content" in sql:
            db.documents = [dict(listing(identity=params[1]), Content="公司收入增长。")]
        return original_select(profile, sql, params, **kwargs)
    adapter = JYDBAnnouncementAdapter(PROFILE, select=select)
    monkeypatch.setattr("ir_search.adapters.jydb.build_jydb_adapter", lambda: adapter)
    inputs = MaterialRequest("收入", [f"jydb://announcement/{identity}" for identity in range(101,111)])
    result = retrieve(inputs)
    assert len(result.materials) == 10
    assert not any(d.code == "operation_budget_exhausted" for d in result.diagnostics)
    limited = retrieve(inputs, context=RequestContext(max_operations=3))
    assert len(limited.materials) == 1 and limited.diagnostics[-1].code == "operation_budget_exhausted"
