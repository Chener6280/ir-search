from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from scripts.bootstrap_cursor_research_workspace import main as bootstrap_main


def test_bootstrap_supports_ir_search_path_and_generated_validation(tmp_path):
    target = tmp_path / "research"
    python_path, ir_search_path = _fake_ir_search_runtime(tmp_path)

    result = bootstrap_main(
        [
            "--target",
            str(target),
            "--ir-search-python",
            str(python_path),
            "--ir-search-path",
            str(ir_search_path),
        ]
    )
    validator = _load_generated_validator(target)
    mcp = json.loads((target / ".cursor" / "mcp.json").read_text(encoding="utf-8"))

    assert result == 0
    assert mcp["mcpServers"]["ir_search"]["env"]["IR_SEARCH_PATH"] == str(ir_search_path)
    assert mcp["mcpServers"]["ir_search"]["env"]["IR_SEARCH_LIVE"] == "0"
    assert validator.validate_workspace(target, mode="generated") == []


def test_bootstrap_overwrite_refreshes_generated_validator(tmp_path):
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
    (target / "scripts" / "validate_workspace.py").write_text("# stale validator\n", encoding="utf-8")

    assert bootstrap_main(
        [
            "--target",
            str(target),
            "--ir-search-python",
            str(python_path),
            "--ir-search-path",
            str(ir_search_path),
            "--overwrite",
        ]
    ) == 0

    validator = _load_generated_validator(target)
    assert validator.VALIDATOR_VERSION == "2026-07-03-r8"


def _load_generated_validator(target: Path):
    validator_path = target / "scripts" / "validate_workspace.py"
    spec = importlib.util.spec_from_file_location("generated_workspace_validator", validator_path)
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
