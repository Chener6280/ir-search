"""MCP wrappers must tell a caller mistake apart from a service or source failure."""
import json

import pytest

from ir_search import mcp_server


def only(payload):
    assert payload["status"] == "error" and payload["schema_version"] == "1.0"
    (diagnostic,) = payload["diagnostics"]
    json.dumps(payload, allow_nan=False)
    return diagnostic


@pytest.mark.parametrize("call,field", [
    (lambda: mcp_server.get_data_payload({"dataset": "prices_daily", "limit": 0}), "limit"),
    (lambda: mcp_server.get_data_payload({"dataset": "prices_daily", "unknown_argument": 1}), "unknown_argument"),
    (lambda: mcp_server.get_data_payload({"dataset": "prices_daily"}, timeout_seconds=0), "timeout_seconds"),
    (lambda: mcp_server.search_materials_payload({"question": "q", "max_chars": 0}), "max_chars"),
    (lambda: mcp_server.search_materials_payload({"question": "q"}, audit_dir="bad\x00dir"), "audit_dir"),
    (lambda: mcp_server.retrieve_payload("q", ["https://example.test/a"], max_spans=0), "max_spans"),
    (lambda: mcp_server.describe_dataset_payload(""), "dataset"),
    (lambda: mcp_server.search_announcements_payload(["600519.SH"], "2026-01-01", "2026-01-31", limit=0), "limit"),
])
def test_rejected_arguments_name_the_field(call, field):
    diagnostic = only(call())
    assert diagnostic["code"] == "invalid_request" and field in diagnostic["detail"]


def test_credential_bearing_urls_are_rejected_without_being_echoed():
    diagnostic = only(mcp_server.retrieve_payload("q", ["https://example.test/a?token=must_not_escape"]))
    assert diagnostic["code"] == "invalid_request" and "must_not_escape" not in json.dumps(diagnostic)
    assert "detail" not in diagnostic or "://" not in diagnostic["detail"]


class Broken:
    def entries(self, *args, **kwargs):
        raise ValueError("postgres://user:must_not_escape@host/db")

    def __getattr__(self, name):
        raise TypeError("must_not_escape")


@pytest.mark.parametrize("call,operation", [
    (lambda: mcp_server.get_data_payload({"dataset": "prices_daily", "symbols": ["600519.SH"],
                                          "start": "2026-01-01", "end": "2026-01-31"}, registry=Broken()), "query_data"),
    (lambda: mcp_server.search_materials_payload({"question": "q", "providers": ["web"],
                                                  "published_start": "2026-01-01", "published_end": "2026-01-31"},
                                                 registry=Broken()), "search_materials"),
])
def test_failures_after_a_valid_request_are_not_blamed_on_the_arguments(call, operation):
    diagnostic = only(call())
    assert diagnostic["code"] == "internal_error" and diagnostic["operation"] == operation
    assert diagnostic["exception_type"] in {"ValueError", "TypeError"}
    assert "must_not_escape" not in json.dumps(diagnostic) and "Do not rewrite" in diagnostic["message"]


def test_capability_listing_failures_are_structured(monkeypatch):
    monkeypatch.setattr(mcp_server, "list_capabilities_impl", lambda: (_ for _ in ()).throw(ValueError("must_not_escape")))
    diagnostic = only(mcp_server.list_capabilities_payload())
    assert diagnostic["code"] == "internal_error" and "must_not_escape" not in json.dumps(diagnostic)
