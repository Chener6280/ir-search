"""The legacy Cursor workspace bootstrap is macOS/Linux only, and renders JSON.

Two contracts live here:

* on Windows the entry point refuses fast, prints an actionable bilingual note
  and creates nothing (verified natively, and on every platform by patching the
  platform predicate so Linux/macOS CI covers the branch too);
* ``render_mcp_json`` escapes substituted values for the JSON string context, so
  a path carrying backslashes or a double quote still produces parseable JSON.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts import bootstrap_cursor_research_workspace as bootstrap
from scripts.bootstrap_cursor_research_workspace import main as bootstrap_main


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = REPO_ROOT / "templates" / "cursor-research-workspace"


def _argv(target: Path, tmp_path: Path) -> list[str]:
    return [
        "--target", str(target),
        "--ir-search-python", str(tmp_path / "python"),
        "--ir-search-path", str(tmp_path / "ir-search"),
    ]


def _assert_refusal(result: int, output: str, target: Path) -> None:
    assert result == 2
    assert not target.exists()
    assert "macOS/Linux" in output and "仅支持 macOS/Linux" in output
    assert "ir-search-mcp.exe" in output
    assert "IR_SEARCH_CREDENTIALS_FILE" in output
    assert "Nothing was created." in output and "没有生成任何文件" in output


@pytest.mark.skipif(os.name != "nt", reason="native check for the Windows refusal path")
def test_bootstrap_refuses_natively_on_windows(tmp_path, capsys):
    target = tmp_path / "research"

    result = bootstrap_main(_argv(target, tmp_path))

    _assert_refusal(result, capsys.readouterr().out, target)


def test_bootstrap_refuses_when_platform_predicate_reports_windows(tmp_path, capsys, monkeypatch):
    """Same branch, forced on, so POSIX CI covers it as well."""
    monkeypatch.setattr(bootstrap, "_is_windows", lambda: True)
    target = tmp_path / "research"

    result = bootstrap_main(_argv(target, tmp_path))

    _assert_refusal(result, capsys.readouterr().out, target)


def test_bootstrap_refuses_before_a_dry_run_reports_anything(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(bootstrap, "_is_windows", lambda: True)
    target = tmp_path / "research"

    result = bootstrap_main(_argv(target, tmp_path) + ["--dry-run"])
    output = capsys.readouterr().out

    assert result == 2
    assert "[DRY-RUN]" not in output
    assert not target.exists()


def test_render_mcp_json_escapes_values_for_the_json_string_context():
    template = (TEMPLATE_ROOT / ".cursor" / "mcp.json.template").read_text(encoding="utf-8")
    workspace = "C:\\Users\\analyst\\Desktop\\research"          # backslashes: "Invalid \\escape" without escaping
    quoted = 'C:\\odd"name\\articles'                          # an embedded double quote closes the JSON string
    tabbed = "C:\\cache\twith\ttabs"                          # a raw control character is illegal in JSON
    replacements = {
        "{{WORKSPACE_ROOT}}": workspace,
        "{{IR_SEARCH_PYTHON}}": "C:\\venv\\Scripts\\python.exe",
        "{{IR_SEARCH_PATH}}": "C:\\repo\\ir_search",
        "{{IR_SEARCH_ENV_FILE}}": "",
        "{{IR_SEARCH_LIVE}}": "0",
        "{{MANUAL_WECHAT_ROOT}}": quoted,
        "{{IR_SEARCH_CACHE_DIR}}": tabbed,
    }

    mcp = json.loads(bootstrap.render_mcp_json(template, replacements))
    server = mcp["mcpServers"]["ir_search"]

    assert server["args"] == [workspace + "/scripts/run_ir_search_mcp.sh"]
    assert server["env"]["IR_SEARCH_PYTHON"] == "C:\\venv\\Scripts\\python.exe"
    assert server["env"]["IR_SEARCH_PATH"] == "C:\\repo\\ir_search"
    assert server["env"]["MANUAL_WECHAT_ROOT"] == quoted
    assert server["env"]["IR_SEARCH_CACHE_DIR"] == tabbed
    assert server["env"]["IR_SEARCH_ENV_FILE"] == ""


def test_render_mcp_json_leaves_plain_posix_values_byte_for_byte():
    template = (TEMPLATE_ROOT / ".cursor" / "mcp.json.template").read_text(encoding="utf-8")
    replacements = {
        "{{WORKSPACE_ROOT}}": "/Users/analyst/research",
        "{{IR_SEARCH_PYTHON}}": "/opt/ir-search/.venv/bin/python",
        "{{IR_SEARCH_PATH}}": "/opt/ir-search",
        "{{IR_SEARCH_ENV_FILE}}": "",
        "{{IR_SEARCH_LIVE}}": "0",
        "{{MANUAL_WECHAT_ROOT}}": "/Users/analyst/research/sources/manual_wechat_articles",
        "{{IR_SEARCH_CACHE_DIR}}": "/Users/analyst/research/.ir_search_cache",
    }

    rendered = bootstrap.render_mcp_json(template, replacements)
    naive = template
    for needle, value in replacements.items():
        naive = naive.replace(needle, value)

    assert rendered == naive
    assert json.loads(rendered)["mcpServers"]["ir_search"]["command"] == "/bin/zsh"


def test_render_mcp_files_writes_parseable_json_for_a_windows_style_target(tmp_path):
    """``render_mcp_files`` itself, bypassing the platform refusal in ``main``."""
    target = tmp_path / "research"
    (target / ".cursor").mkdir(parents=True)
    (target / ".cursor" / "mcp.json.template").write_text(
        (TEMPLATE_ROOT / ".cursor" / "mcp.json.template").read_text(encoding="utf-8"), encoding="utf-8"
    )
    replacements = {
        "{{WORKSPACE_ROOT}}": r"C:\Users\analyst\Desktop\research",
        "{{IR_SEARCH_PYTHON}}": r"C:\venv\Scripts\python.exe",
        "{{IR_SEARCH_PATH}}": r"C:\repo\ir_search",
        "{{IR_SEARCH_ENV_FILE}}": "",
        "{{IR_SEARCH_LIVE}}": "0",
        "{{MANUAL_WECHAT_ROOT}}": r"C:\Users\analyst\Desktop\research\sources",
        "{{IR_SEARCH_CACHE_DIR}}": r"C:\Users\analyst\Desktop\research\.ir_search_cache",
    }

    bootstrap.render_mcp_files(target, replacements, overwrite=True)

    for name in ("mcp.json", "mcp.json.example"):
        mcp = json.loads((target / ".cursor" / name).read_text(encoding="utf-8"))
        env = mcp["mcpServers"]["ir_search"]["env"]
        assert env["IR_SEARCH_PYTHON"] == r"C:\venv\Scripts\python.exe"
        assert env["IR_SEARCH_PATH"] == r"C:\repo\ir_search"
