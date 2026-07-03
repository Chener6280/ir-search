from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from scripts.bootstrap_cursor_research_workspace import main as bootstrap_main


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = REPO_ROOT / "templates" / "cursor-research-workspace"


def test_generated_mode_fails_if_selected_python_lacks_mcp(tmp_path):
    target = tmp_path / "research"
    ok_python, ir_search_path = _fake_runtime(tmp_path, mode="ok")
    missing_mcp_python, _ = _fake_runtime(tmp_path, mode="missing_mcp")
    assert bootstrap_main(["--target", str(target), "--ir-search-python", str(ok_python), "--ir-search-path", str(ir_search_path)]) == 0
    mcp_path = target / ".cursor" / "mcp.json"
    mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
    mcp["mcpServers"]["ir_search"]["env"]["IR_SEARCH_PYTHON"] = str(missing_mcp_python)
    mcp_path.write_text(json.dumps(mcp, ensure_ascii=False, indent=2), encoding="utf-8")

    errors = _load_validator_from(target).validate_workspace(target, mode="generated")

    assert any("MCP runtime check failed" in error for error in errors)
    assert any("cannot import mcp.server.fastmcp.FastMCP" in error for error in errors)
    assert any(f'{missing_mcp_python} -m pip install -e "{ir_search_path}[mcp]"' in error for error in errors)


def test_generated_mode_skip_mcp_runtime_check_warns(tmp_path):
    target = tmp_path / "research"
    missing_mcp_python, ir_search_path = _fake_runtime(tmp_path, mode="missing_mcp")
    assert (
        bootstrap_main(
            [
                "--target",
                str(target),
                "--ir-search-python",
                str(missing_mcp_python),
                "--ir-search-path",
                str(ir_search_path),
                "--skip-mcp-runtime-check",
            ]
        )
        == 0
    )

    errors, warnings = _load_validator_from(target).collect_validation_issues(
        target,
        mode="generated",
        skip_mcp_runtime_check=True,
    )

    assert errors == []
    assert any("MCP runtime check skipped" in warning for warning in warnings)


def test_template_mode_skips_mcp_runtime_check(capsys):
    result = _load_validator_from(TEMPLATE_ROOT).main([str(TEMPLATE_ROOT), "--mode", "template"])
    output = capsys.readouterr().out

    assert result == 0
    assert "MCP runtime check skipped in template mode" in output


def _load_validator_from(root: Path):
    validator_path = root / "scripts" / "validate_workspace.py"
    spec = importlib.util.spec_from_file_location("validator_runtime_test", validator_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    return python_path.resolve(), ir_search_path.resolve()
