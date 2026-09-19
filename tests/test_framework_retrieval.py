from datetime import datetime, timezone
import json

import pytest

from ir_search import MaterialRequest, RequestContext, Status, retrieve
from ir_search.documents.models import Document
from ir_search.models import EvidenceType, SourceTier
from ir_search.services import retrieval


def document(url="https://example.com/report"):
    return Document(
        doc_id="fixture_document", url=url, canonical_url=url, title="Revenue report",
        source="test_fixture", source_tier=SourceTier.COMPANY, evidence_type=EvidenceType.FINANCIAL_REPORT,
        content_type="text/html", published_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 9, 13, tzinfo=timezone.utc), extraction_method="html_parser",
        text="Revenue increased by 20 percent. Cash flow from operations increased during the quarter.",
    )


def test_retrieve_returns_source_text_and_locatable_spans(monkeypatch):
    doc = document()
    calls = []

    def fetch(url, **kwargs):
        calls.append(kwargs)
        return doc

    monkeypatch.setattr(retrieval, "read_web_document", fetch)
    result = retrieve(MaterialRequest("revenue", [doc.url]))
    assert result.status == Status.OK and result.source_text_trust == "untrusted"
    material = result.materials[0]
    assert material.text == doc.text and material.provenance.source_tier == SourceTier.COMPANY
    assert material.evidence_spans
    span = material.evidence_spans[0]
    assert span["doc_id"] == doc.doc_id and span["text"] in doc.text
    assert span["start_char"] is not None
    assert 0 < calls[0]["context"].remaining_seconds() <= 30 and calls[0]["mode"] == "auto"
    json.dumps(result.to_dict(), allow_nan=False)


def test_one_failed_url_does_not_hide_success_or_exception_secret(monkeypatch):
    def fetch(url, **kwargs):
        if url.endswith("bad"):
            raise RuntimeError("Authorization: Bearer must_not_escape")
        return document(url)

    monkeypatch.setattr(retrieval, "read_web_document", fetch)
    result = retrieve(MaterialRequest("revenue", ["https://example.com/bad", "https://example.com/good"]))
    assert result.status == Status.PARTIAL and len(result.materials) == 1
    assert result.diagnostics[0].code == "document_processing_failed"
    assert "must_not_escape" not in json.dumps(result.to_dict())


def test_blocked_urls_never_reach_fetch(monkeypatch):
    monkeypatch.setattr(retrieval, "read_web_document", lambda *a, **k: pytest.fail("unexpected network call"))
    result = retrieve(MaterialRequest("revenue", ["file:///tmp/private", "http://127.0.0.1/report"]))
    assert result.status == Status.UNAVAILABLE
    assert [d.code for d in result.diagnostics] == ["blocked_url", "blocked_url"]


def test_credentials_are_rejected_before_any_network_call(monkeypatch):
    monkeypatch.setattr(retrieval, "read_web_document", lambda *a, **k: pytest.fail("unexpected network call"))
    with pytest.raises(ValueError, match="credential-free"):
        retrieve(MaterialRequest("revenue", ["https://example.com/good", "https://example.com/?token=must_not_escape"]))


def test_missing_dates_truncation_and_snippets_are_visible(monkeypatch):
    doc = document()
    doc.published_at = None
    doc.warnings = ["text truncated", "vendor warning includes must_not_escape"]
    doc.content_type = "snippet"
    doc.extraction_method = "search_hit_snippet_fallback"
    monkeypatch.setattr(retrieval, "read_web_document", lambda *a, **k: doc)
    result = retrieve(MaterialRequest("revenue", [doc.url], max_chars=60))
    material = result.materials[0]
    assert result.status == Status.PARTIAL and material.text_origin == "search_snippet"
    assert {"published_date_unknown", "text_truncated", "not_full_text"} <= set(material.warnings)
    assert len(material.text) <= 60
    assert "must_not_escape" not in json.dumps(result.to_dict())


def test_invalid_provider_metadata_is_isolated(monkeypatch):
    def fetch(url, **kwargs):
        doc = document(url)
        if url.endswith("bad"):
            doc.extra["adapter_mode"] = "invalid_mode"
        return doc

    monkeypatch.setattr(retrieval, "read_web_document", fetch)
    result = retrieve(MaterialRequest("revenue", ["https://example.com/bad", "https://example.com/good"]))
    assert result.status == Status.PARTIAL and len(result.materials) == 1


def test_mock_document_is_never_promoted_to_live(monkeypatch):
    doc = document()
    doc.extra["adapter_mode"] = "mock"
    monkeypatch.setattr(retrieval, "read_web_document", lambda *a, **k: doc)
    result = retrieve(MaterialRequest("revenue", [doc.url]))
    assert not result.materials and result.diagnostics[0].adapter_mode.value == "mock"


def test_empty_text_and_fetch_diagnostics_are_not_true_negatives(monkeypatch):
    doc = document()
    doc.text = ""
    doc.errors = ["blocked_by_policy: private redirect with must_not_escape"]
    monkeypatch.setattr(retrieval, "read_web_document", lambda *a, **k: doc)
    result = retrieve(MaterialRequest("revenue", [doc.url]))
    assert result.status == Status.UNAVAILABLE
    assert {d.code for d in result.diagnostics} == {"blocked_url", "no_extracted_text"}
    assert "must_not_escape" not in json.dumps(result.to_dict())


def test_budget_exhaustion_keeps_completed_material_but_stops_more_calls(monkeypatch):
    calls = []

    def fetch(url, **kwargs):
        kwargs["context"].begin_operation()
        calls.append(url)
        return document(url)

    monkeypatch.setattr(retrieval, "read_web_document", fetch)
    result = retrieve(MaterialRequest("revenue", ["https://example.com/a", "https://example.com/b"]),
                      context=RequestContext(max_operations=1))
    assert len(calls) == 1 and len(result.materials) == 1
    assert result.status == Status.PARTIAL and result.diagnostics[-1].code == "operation_budget_exhausted"


def test_cancelled_retrieval_starts_no_fetch(monkeypatch):
    monkeypatch.setattr(retrieval, "read_web_document", lambda *a, **k: pytest.fail("unexpected network call"))
    context = RequestContext()
    context.cancel()
    assert retrieve(MaterialRequest("revenue", ["https://example.com/a"]), context=context).diagnostics[0].code == "cancelled"
    with pytest.raises(ValueError):
        retrieve("invalid request")


def test_generated_content_stays_an_opinion(monkeypatch):
    doc = document()
    doc.extra["generated"] = True
    doc.extraction_method = "ocr"
    monkeypatch.setattr(retrieval, "read_web_document", lambda *a, **k: doc)
    material = retrieve(MaterialRequest("revenue", [doc.url])).materials[0]
    assert material.text_origin == "generated_text" and material.provenance.generated
    assert material.provenance.evidence_type == EvidenceType.OPINION
    assert material.evidence_spans[0]["evidence_type"] == "opinion"
    assert doc.evidence_type == EvidenceType.FINANCIAL_REPORT  # The fetched object is not mutated.
