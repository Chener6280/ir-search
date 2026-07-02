from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from scripts.bootstrap_cursor_research_workspace import main as bootstrap_main


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = REPO_ROOT / "templates" / "cursor-research-workspace"


def test_workspace_template_files_exist():
    required = [
        "README.md",
        "AGENTS.md",
        ".cursorignore",
        ".cursorindexingignore",
        ".env.example",
        ".cursor/mcp.json.template",
        ".cursor/rules/20-ir-search-evidence-policy.mdc",
        "prompts/R-FINANCE-WEB.md",
        "prompts/README.md",
        "notes/smoke_test_checklist.md",
        "docs/onboarding.md",
        "scripts/bootstrap_workspace.sh",
        "scripts/run_ir_search_mcp.sh",
        "scripts/validate_workspace.py",
        "scripts/smoke_test_checklist.md",
        "sources/papers/sample_monetary_policy_transmission.md",
    ]

    for rel in required:
        assert (TEMPLATE_ROOT / rel).exists(), rel


def test_bootstrap_dry_run_succeeds(tmp_path):
    target = tmp_path / "research"
    python_path, ir_search_path = _fake_ir_search_runtime(tmp_path)

    assert bootstrap_main(["--target", str(target), "--ir-search-python", str(python_path), "--ir-search-path", str(ir_search_path), "--dry-run"]) == 0
    assert not target.exists()


def test_bootstrap_generates_valid_workspace(tmp_path):
    target = tmp_path / "research"
    python_path, ir_search_path = _fake_ir_search_runtime(tmp_path)

    assert bootstrap_main(["--target", str(target), "--ir-search-python", str(python_path), "--ir-search-path", str(ir_search_path)]) == 0
    mcp = json.loads((target / ".cursor" / "mcp.json.example").read_text(encoding="utf-8"))

    assert mcp["mcpServers"]["ir_search"]["command"] == "/bin/zsh"
    assert mcp["mcpServers"]["ir_search"]["args"] == [str(target.resolve() / "scripts" / "run_ir_search_mcp.sh")]
    assert mcp["mcpServers"]["ir_search"]["env"]["IR_SEARCH_PYTHON"] == str(python_path)
    assert mcp["mcpServers"]["ir_search"]["env"]["IR_SEARCH_PATH"] == str(ir_search_path)
    assert mcp["mcpServers"]["ir_search"]["env"]["IR_SEARCH_LIVE"] == "0"
    assert (target / ".cursor" / "mcp.json").exists()
    assert (target / "sources" / "manual_wechat_articles").exists()
    assert (target / ".ir_search_cache").exists()


def test_validate_workspace_detects_missing_file(tmp_path):
    validator = _load_validator()
    target = tmp_path / "bad"
    target.mkdir()

    errors = validator.validate_workspace(target)

    assert any("Missing file" in error for error in errors)


def test_validate_workspace_detects_secret_and_personal_path(tmp_path):
    target = tmp_path / "research"
    python_path, ir_search_path = _fake_ir_search_runtime(tmp_path)
    assert bootstrap_main(["--target", str(target), "--ir-search-python", str(python_path), "--ir-search-path", str(ir_search_path)]) == 0
    (target / "notes" / "manual_verification_log.md").write_text("token=real-secret\n/Users/alice/private\n", encoding="utf-8")

    validator = _load_validator()
    errors, warnings = validator.collect_validation_issues(target)
    strict_errors = validator.validate_workspace(target, strict=True)

    assert any("Possible secret" in error for error in errors)
    assert any("Personal path" in warning for warning in warnings)
    assert any("Personal path" in error for error in strict_errors)


def test_rules_include_required_evidence_constraints():
    rules = "\n".join(path.read_text(encoding="utf-8") for path in (TEMPLATE_ROOT / ".cursor" / "rules").glob("*.mdc"))

    for phrase in [
        "source_health",
        "fallback",
        "search snippets",
        "untrusted",
        "insufficient_evidence",
        "contradicted",
        "claim_ledger",
    ]:
        assert phrase in rules


def _load_validator():
    validator_path = TEMPLATE_ROOT / "scripts" / "validate_workspace.py"
    spec = importlib.util.spec_from_file_location("cursor_workspace_validator_test", validator_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _fake_ir_search_runtime(tmp_path: Path) -> tuple[Path, Path]:
    python_path = tmp_path / "fake-python"
    python_path.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    python_path.chmod(0o755)
    ir_search_path = tmp_path / "fake-ir-search"
    package = ir_search_path / "ir_search"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    return python_path.resolve(), ir_search_path.resolve()
