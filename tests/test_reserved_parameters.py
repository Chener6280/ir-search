from __future__ import annotations

import inspect

from ir_search.mcp_server import fetch_document_payload, verify_claims_payload
from ir_search.models import SearchResult
from ir_search.research.orchestrator import deep_research


def test_fetch_document_payload_discloses_reserved_include_tables():
    payload = fetch_document_payload("file:///etc/passwd", include_tables=True)

    assert payload["reserved_parameters"]["include_tables"]["status"] == "reserved_not_applied"
    assert "table extraction is not implemented" in payload["reserved_parameters"]["include_tables"]["reason"]


def test_fetch_document_payload_omits_reserved_include_tables_when_not_requested():
    payload = fetch_document_payload("file:///etc/passwd", include_tables=False)

    assert "reserved_parameters" not in payload


def test_deep_research_discloses_reserved_language_and_output_style():
    run = deep_research(
        "最近 AI 光模块需求",
        max_searches=1,
        max_documents=1,
        search_fn=lambda q: SearchResult(query=q, hits=[], diagnostics=[]),
        source_health_fn=lambda: {"sources": {}},
    )

    reserved = run.to_dict()["reserved_parameters"]

    assert reserved["language"]["status"] == "reserved_not_applied"
    assert reserved["output_style"]["status"] == "reserved_not_applied"


def test_verify_claims_payload_does_not_expose_search_fn():
    signature = inspect.signature(verify_claims_payload)

    assert "search_fn" not in signature.parameters
    assert verify_claims_payload(["公司收入增长"], evidence_spans=[])["claim_ledger"][0]["status"] == "insufficient_evidence"
