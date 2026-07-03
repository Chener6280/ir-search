from __future__ import annotations

import json
from pathlib import Path

from scripts.doctor_ir_search_mcp import EXPECTED_TOOLS, main as doctor_main, run_diagnostics


def test_doctor_succeeds_when_runtime_has_mcp(tmp_path):
    python_path, ir_search_path = _fake_runtime(tmp_path, mode="ok")

    diagnostics = run_diagnostics(ir_search_python=python_path, ir_search_path=ir_search_path)

    assert diagnostics["ok"] is True
    assert diagnostics["checks"]["import_ir_search"] is True
    assert diagnostics["checks"]["import_fastmcp"] is True
    assert set(diagnostics["tool_names"]) == EXPECTED_TOOLS


def test_doctor_reports_missing_mcp_with_install_command(tmp_path):
    python_path, ir_search_path = _fake_runtime(tmp_path, mode="missing_mcp")

    diagnostics = run_diagnostics(ir_search_python=python_path, ir_search_path=ir_search_path)

    assert diagnostics["ok"] is False
    assert diagnostics["checks"]["import_ir_search"] is True
    assert diagnostics["checks"]["import_fastmcp"] is False
    assert "cannot import mcp.server.fastmcp.FastMCP" in "\n".join(diagnostics["errors"])
    assert f'{python_path} -m pip install -e "{ir_search_path}[mcp]"' == diagnostics["fix"]["install"]


def test_doctor_reports_missing_ir_search(tmp_path):
    python_path, ir_search_path = _fake_runtime(tmp_path, mode="missing_ir_search")

    diagnostics = run_diagnostics(ir_search_python=python_path, ir_search_path=ir_search_path)

    assert diagnostics["ok"] is False
    assert diagnostics["checks"]["import_ir_search"] is False


def test_doctor_json_output_is_valid(tmp_path, capsys):
    python_path, ir_search_path = _fake_runtime(tmp_path, mode="ok")

    result = doctor_main(["--ir-search-python", str(python_path), "--ir-search-path", str(ir_search_path), "--json"])
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert result == 0
    assert payload["ok"] is True
    assert set(payload["tool_names"]) == EXPECTED_TOOLS


def _fake_runtime(tmp_path: Path, *, mode: str) -> tuple[Path, Path]:
    python_path = tmp_path / f"fake-python-{mode}"
    python_path.write_text(
        "#!/usr/bin/env bash\n"
        "code=\"${2:-}\"\n"
        f"mode={mode!r}\n"
        "if [[ \"$mode\" == \"missing_ir_search\" && \"$code\" == *\"import ir_search\"* ]]; then\n"
        "  echo 'ModuleNotFoundError: No module named ir_search' >&2\n"
        "  exit 1\n"
        "fi\n"
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
