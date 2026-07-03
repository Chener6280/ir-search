from __future__ import annotations

import importlib.util
from pathlib import Path

from scripts.bootstrap_cursor_research_workspace import main as bootstrap_main


TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "templates" / "cursor-research-workspace"


def test_validator_allows_placeholders_but_flags_real_secret():
    validator = _load_validator()

    assert not validator._contains_secret('BOCHA_API_KEY="${env:BOCHA_API_KEY}"')
    assert not validator._contains_secret("token=replace-me")
    assert validator._contains_secret("api_key=abc123456789")
    assert validator._contains_secret("cookie=sessionid=abc123456789")
    assert validator._contains_secret("authorization=" + "Bearer" + " " + "abcdefghijk")
    assert validator._contains_secret("refresh_token=abc123456789")


def test_validator_personal_path_warning_and_strict_error(tmp_path):
    target = tmp_path / "research"
    python_path, ir_search_path = _fake_ir_search_runtime(tmp_path)
    assert bootstrap_main(["--target", str(target), "--ir-search-python", str(python_path), "--ir-search-path", str(ir_search_path)]) == 0
    (target / "notes" / "manual_verification_log.md").write_text("/Users/alice/private\n", encoding="utf-8")

    errors, warnings = _load_validator().collect_validation_issues(target)
    strict_errors = _load_validator().validate_workspace(target, strict=True)

    assert not any("Personal path" in error for error in errors)
    assert any("Personal path" in warning for warning in warnings)
    assert any("Personal path" in error for error in strict_errors)


def test_validator_accepts_wrapper_mcp_without_env_expansion_warning(tmp_path):
    target = tmp_path / "research"
    python_path, ir_search_path = _fake_ir_search_runtime(tmp_path)
    assert bootstrap_main(["--target", str(target), "--ir-search-python", str(python_path), "--ir-search-path", str(ir_search_path)]) == 0

    errors, warnings = _load_validator().collect_validation_issues(target)

    assert not errors
    assert not any("${env:KEY}" in warning for warning in warnings)


def test_validator_rejects_unreplaced_absolute_placeholders(tmp_path):
    target = tmp_path / "research"
    python_path, ir_search_path = _fake_ir_search_runtime(tmp_path)
    assert bootstrap_main(["--target", str(target), "--ir-search-python", str(python_path), "--ir-search-path", str(ir_search_path)]) == 0
    mcp_path = target / ".cursor" / "mcp.json"
    mcp_text = mcp_path.read_text(encoding="utf-8")
    mcp_path.write_text(mcp_text.replace(str(python_path), "/ABSOLUTE/PATH/TO/python"), encoding="utf-8")

    errors = _load_validator().validate_workspace(target)

    assert any("/ABSOLUTE/PATH/TO" in error for error in errors)


def test_validator_rejects_missing_mcp_python(tmp_path):
    target = tmp_path / "research"
    python_path, ir_search_path = _fake_ir_search_runtime(tmp_path)
    assert bootstrap_main(["--target", str(target), "--ir-search-python", str(python_path), "--ir-search-path", str(ir_search_path)]) == 0
    mcp_path = target / ".cursor" / "mcp.json"
    missing_python = tmp_path / "missing-python"
    mcp_path.write_text(mcp_path.read_text(encoding="utf-8").replace(str(python_path), str(missing_python)), encoding="utf-8")

    errors = _load_validator().validate_workspace(target)

    assert any("IR_SEARCH_PYTHON does not exist" in error for error in errors)


def _load_validator():
    validator_path = TEMPLATE_ROOT / "scripts" / "validate_workspace.py"
    spec = importlib.util.spec_from_file_location("cursor_workspace_validator_security_test", validator_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
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
