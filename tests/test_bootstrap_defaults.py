from __future__ import annotations

import json
from pathlib import Path

from scripts.bootstrap_cursor_research_workspace import main as bootstrap_main


def test_bootstrap_defaults_to_mock_safe_live_mode(tmp_path):
    target = tmp_path / "research"
    python_path, ir_search_path = _fake_ir_search_runtime(tmp_path)

    assert bootstrap_main(["--target", str(target), "--ir-search-python", str(python_path), "--ir-search-path", str(ir_search_path)]) == 0
    mcp = json.loads((target / ".cursor" / "mcp.json").read_text(encoding="utf-8"))

    assert mcp["mcpServers"]["ir_search"]["env"]["IR_SEARCH_LIVE"] == "0"
    assert mcp["mcpServers"]["ir_search"]["env"]["IR_SEARCH_ENV_FILE"] == ""


def test_bootstrap_live_mode_without_keys_warns(tmp_path, monkeypatch, capsys):
    for key in ["BOCHA_API_KEY", "EXA_API_KEY", "TAVILY_API_KEY", "ANYSEARCH_API_KEY"]:
        monkeypatch.delenv(key, raising=False)

    target = tmp_path / "research"
    python_path, ir_search_path = _fake_ir_search_runtime(tmp_path)
    assert bootstrap_main(["--target", str(target), "--ir-search-python", str(python_path), "--ir-search-path", str(ir_search_path), "--ir-search-live", "1"]) == 0

    captured = capsys.readouterr()
    assert "IR_SEARCH_LIVE=1" in captured.out
    assert "R-SOURCE-HEALTH" in captured.out


def test_bootstrap_renders_env_file_without_copying_secrets(tmp_path):
    target = tmp_path / "research"
    python_path, ir_search_path = _fake_ir_search_runtime(tmp_path)
    env_file = tmp_path / "ir_search.env"
    env_file.write_text("BOCHA_API_KEY=secret-value\n", encoding="utf-8")

    assert bootstrap_main([
        "--target",
        str(target),
        "--ir-search-python",
        str(python_path),
        "--ir-search-path",
        str(ir_search_path),
        "--env-file",
        str(env_file),
    ]) == 0
    mcp_text = (target / ".cursor" / "mcp.json").read_text(encoding="utf-8")

    assert str(env_file.resolve()) in mcp_text
    assert "secret-value" not in mcp_text


def _fake_ir_search_runtime(tmp_path: Path) -> tuple[Path, Path]:
    python_path = tmp_path / "fake-python"
    python_path.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    python_path.chmod(0o755)
    ir_search_path = tmp_path / "fake-ir-search"
    package = ir_search_path / "ir_search"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    return python_path.resolve(), ir_search_path.resolve()
