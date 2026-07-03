from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

from scripts.bootstrap_cursor_research_workspace import main as bootstrap_main


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = REPO_ROOT / "templates" / "cursor-research-workspace"


def test_template_mode_does_not_require_rendered_mcp():
    validator = _load_validator()

    errors, warnings = validator.collect_validation_issues(TEMPLATE_ROOT, mode="template")

    assert validator.VALIDATOR_VERSION == "2026-07-03-r9"
    assert not any("Missing .cursor/mcp.json" in error for error in errors)
    assert any("Missing .cursor/mcp.json" in warning for warning in warnings)


def test_generated_mode_requires_rendered_mcp():
    validator = _load_validator()

    errors = validator.validate_workspace(TEMPLATE_ROOT, mode="generated")

    assert any("Missing .cursor/mcp.json" in error for error in errors)


def test_generated_mode_rejects_unreplaced_placeholders(tmp_path):
    target = tmp_path / "placeholder-workspace"
    shutil.copytree(TEMPLATE_ROOT, target)
    template_text = (target / ".cursor" / "mcp.json.template").read_text(encoding="utf-8")
    (target / ".cursor" / "mcp.json").write_text(template_text, encoding="utf-8")

    errors = _load_validator().validate_workspace(target, mode="generated")

    assert any("Unreplaced placeholder" in error for error in errors)


def test_bootstrap_output_validates_in_generated_mode(tmp_path):
    target = tmp_path / "research"
    python_path, ir_search_path = _fake_ir_search_runtime(tmp_path)

    assert (
        bootstrap_main(
            [
                "--target",
                str(target),
                "--ir-search-python",
                str(python_path),
                "--ir-search-path",
                str(ir_search_path),
            ]
        )
        == 0
    )
    errors = _load_validator_from(target).validate_workspace(target, mode="generated")
    mcp = json.loads((target / ".cursor" / "mcp.json").read_text(encoding="utf-8"))

    assert errors == []
    assert mcp["mcpServers"]["ir_search"]["env"]["IR_SEARCH_PATH"] == str(ir_search_path)


def test_validator_cli_prints_version(capsys):
    result = _load_validator().main([str(TEMPLATE_ROOT), "--mode", "template"])
    output = capsys.readouterr().out

    assert result == 0
    assert "[INFO] validate_workspace version: 2026-07-03-r9" in output


def _load_validator():
    return _load_validator_from(TEMPLATE_ROOT)


def _load_validator_from(root: Path):
    validator_path = root / "scripts" / "validate_workspace.py"
    spec = importlib.util.spec_from_file_location("cursor_workspace_validator_test", validator_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_ir_search_runtime(tmp_path: Path) -> tuple[Path, Path]:
    python_path = tmp_path / "fake-python"
    python_path.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"${1:-}\" == \"-c\" && \"${2:-}\" == *\"list_tool_names\"* ]]; then\n"
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
