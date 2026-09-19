"""MCP path arguments come from a model that reads untrusted text: writes stay under one root."""
import json
from pathlib import Path

import pytest

from ir_search import mcp_server


def rejected(payload, field):
    (diagnostic,) = payload["diagnostics"]
    return payload["status"] == "error" and diagnostic["code"] == "invalid_request" and field in diagnostic["detail"]


def test_default_root_sits_beside_the_private_credentials_file(tmp_path, monkeypatch):
    monkeypatch.delenv("IR_SEARCH_OUTPUT_ROOT", raising=False)
    monkeypatch.setenv("IR_SEARCH_CREDENTIALS_FILE", str(tmp_path / "credentials.env"))
    root = (tmp_path / ".local" / "exports").resolve()
    assert Path(mcp_server._mcp_output_dir("runs/2026", "audit_dir")) == root / "runs" / "2026"
    assert Path(mcp_server._mcp_output_dir(str(root / "a"), "audit_dir")) == root / "a"
    assert mcp_server._mcp_output_dir(None, "audit_dir") is None


@pytest.mark.parametrize("value", ["../outside", "a/../../outside", "~/somewhere-else"])
def test_paths_that_leave_the_root_are_rejected_without_echoing_them(tmp_path, monkeypatch, value):
    monkeypatch.setenv("IR_SEARCH_OUTPUT_ROOT", str(tmp_path / "root"))
    outside = str(tmp_path / "elsewhere")
    for candidate in (value, outside):
        payload = mcp_server.search_materials_payload({"question": "q"}, audit_dir=candidate)
        assert rejected(payload, "audit_dir") and candidate not in json.dumps(payload)
        payload = mcp_server.retrieve_payload("q", ["https://example.test/a"], archive_dir=candidate)
        assert rejected(payload, "archive_dir")
    assert not (tmp_path / "elsewhere").exists() and not (tmp_path / "outside").exists()


def test_symlinked_folder_cannot_escape_the_root(tmp_path, monkeypatch):
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir(); outside.mkdir()
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation requires privilege on this host")
    monkeypatch.setenv("IR_SEARCH_OUTPUT_ROOT", str(root))
    with pytest.raises(ValueError):
        mcp_server._mcp_output_dir("link/runs", "audit_dir")


def test_relative_root_setting_is_a_configuration_error(monkeypatch):
    monkeypatch.setenv("IR_SEARCH_OUTPUT_ROOT", "relative/root")
    result = mcp_server.search_materials_payload({"question": "q"}, audit_dir="runs")
    assert result['diagnostics'][0]['code'] == 'invalid_output_root'
    assert result['diagnostics'][0]['key'] == 'IR_SEARCH_OUTPUT_ROOT'
