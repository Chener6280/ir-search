from __future__ import annotations

from pathlib import Path

from scripts.bootstrap_cursor_research_workspace import main as bootstrap_main


def test_bootstrap_runs_mcp_preflight_by_default(tmp_path):
    target = tmp_path / "research"
    python_path, ir_search_path = _fake_runtime(tmp_path, mode="ok")

    result = bootstrap_main(["--target", str(target), "--ir-search-python", str(python_path), "--ir-search-path", str(ir_search_path)])

    assert result == 0
    assert (target / "scripts" / "doctor_ir_search_mcp.py").exists()


def test_bootstrap_fails_if_mcp_runtime_missing(tmp_path, capsys):
    target = tmp_path / "research"
    python_path, ir_search_path = _fake_runtime(tmp_path, mode="missing_mcp")

    result = bootstrap_main(["--target", str(target), "--ir-search-python", str(python_path), "--ir-search-path", str(ir_search_path)])
    output = capsys.readouterr().out

    assert result != 0
    assert "cannot import mcp.server.fastmcp.FastMCP" in output
    assert f'{python_path} -m pip install -e "{ir_search_path}[mcp]"' in output
    assert "[OK] Workspace created." not in output


def test_bootstrap_skip_mcp_runtime_check_warns_and_succeeds(tmp_path, capsys):
    target = tmp_path / "research"
    python_path, ir_search_path = _fake_runtime(tmp_path, mode="missing_mcp")

    result = bootstrap_main(
        [
            "--target",
            str(target),
            "--ir-search-python",
            str(python_path),
            "--ir-search-path",
            str(ir_search_path),
            "--skip-mcp-runtime-check",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert "MCP runtime check skipped" in output
    assert (target / ".cursor" / "mcp.json").exists()
    assert (target / "scripts" / "doctor_ir_search_mcp.py").exists()


def _fake_runtime(tmp_path: Path, *, mode: str) -> tuple[Path, Path]:
    python_path = tmp_path / f"fake-python-{mode}"
    python_path.write_text(
        "#!/usr/bin/env bash\n"
        "code=\"${2:-}\"\n"
        f"mode={mode!r}\n"
        "if [[ \"$mode\" == \"missing_mcp\" && \"$code\" == *\"mcp.server.fastmcp\"* ]]; then\n"
        "  echo 'ModuleNotFoundError: No module named mcp' >&2\n"
        "  exit 1\n"
        "fi\n"
        "if [[ \"$code\" == *\"list_tool_names\"* ]]; then\n"
        "  echo '[\"search\", \"fetch_document\", \"extract_evidence\", \"verify_claims\", \"deep_research\", \"source_health\"]'\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    python_path.chmod(0o755)
    ir_search_path = tmp_path / "fake-ir-search"
    package = ir_search_path / "ir_search"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    return python_path.resolve(), ir_search_path.resolve()
