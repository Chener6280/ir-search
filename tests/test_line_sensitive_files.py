from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = REPO_ROOT / "templates" / "cursor-research-workspace"


def _raw(path: Path) -> bytes:
    return path.read_bytes()


def _text(path: Path) -> str:
    return _raw(path).decode("utf-8")


def _lf_line_count(path: Path) -> int:
    data = _raw(path)
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def _assert_lf_multiline(path: Path, minimum: int) -> None:
    data = _raw(path)

    assert b"\r" not in data, path
    assert _lf_line_count(path) >= minimum, path


def _active_lines(path: Path) -> list[str]:
    return [
        line.strip()
        for line in _text(path).split("\n")
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_root_readme_is_lf_multiline_ir_search_readme():
    path = REPO_ROOT / "README.md"
    text = _text(path)

    _assert_lf_multiline(path, 10)
    assert "ir-search" in text
    assert "MCP" in text


def test_mdc_frontmatter_is_lf_multiline():
    for path in sorted((TEMPLATE_ROOT / ".cursor" / "rules").glob("*.mdc")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        _assert_lf_multiline(path, 8)
        lines = _text(path).split("\n")
        assert lines[0] == "---", rel
        assert "---" in lines[1:], rel
        assert any(line.startswith("description:") for line in lines[1:]), rel
        assert any(line.startswith("alwaysApply:") for line in lines[1:]), rel


def test_ignore_files_have_active_lf_multiline_rules():
    for rel in [".cursorignore", ".cursorindexingignore"]:
        path = TEMPLATE_ROOT / rel
        active = _active_lines(path)

        _assert_lf_multiline(path, 9)
        assert active
        assert active[0] == "/*"

    _assert_lf_multiline(TEMPLATE_ROOT / ".cursorindexingignore", 25)
    _assert_lf_multiline(TEMPLATE_ROOT / ".cursor" / "mcp.json.template", 8)


def test_cursorindexingignore_active_rules_are_complete():
    active = _active_lines(TEMPLATE_ROOT / ".cursorindexingignore")

    assert active[0] == "/*"
    for rule in [
        "!/outputs/reports/",
        "!/outputs/reports/**",
        "!/outputs/memos/",
        "!/outputs/memos/**",
        "!/outputs/source_tables/",
        "!/outputs/source_tables/**",
        "!/scripts/",
        "!/scripts/**",
        "/outputs/raw/",
        "/outputs/raw/**",
        "/outputs/tmp/",
        "/outputs/tmp/**",
    ]:
        assert rule in active


def test_gitattributes_is_lf_multiline():
    path = REPO_ROOT / ".gitattributes"
    lines = _text(path).split("\n")

    _assert_lf_multiline(path, 13)
    for rule in [
        "* text=auto eol=lf",
        "*.py text eol=lf",
        "*.mdc text eol=lf",
        "*.yaml text eol=lf",
        "*.gitignore text eol=lf",
        ".cursorignore text eol=lf",
        ".cursorindexingignore text eol=lf",
    ]:
        assert rule in lines


def test_key_scripts_have_reviewable_lf_line_counts():
    thresholds = {
        REPO_ROOT / "scripts" / "bootstrap_cursor_research_workspace.py": 80,
        REPO_ROOT / "scripts" / "run_acceptance_cases.py": 80,
        REPO_ROOT / "scripts" / "score_acceptance_results.py": 120,
        TEMPLATE_ROOT / "scripts" / "validate_workspace.py": 120,
    }
    for path, minimum in thresholds.items():
        _assert_lf_multiline(path, minimum)


def test_acceptance_cases_are_lf_multiline_and_cover_twenty_cases():
    path = REPO_ROOT / "tests" / "acceptance_cases.yaml"
    text = _text(path)

    _assert_lf_multiline(path, 100)
    assert text.count("  - id:") >= 20
    assert "must_not:" in text


def test_fixture_files_are_lf_multiline():
    for rel in [
        "tests/fixtures/sample_acceptance_output.md",
        "tests/fixtures/reused_run_output.md",
        "tests/fixtures/missing_run_id_output.md",
        "tests/fixtures/media_financial_report_output.md",
        "tests/fixtures/missing_official_gap_report_output.md",
        "tests/fixtures/missing_verify_claims_output.md",
        "tests/fixtures/missing_freshness_bucket_output.md",
    ]:
        _assert_lf_multiline(REPO_ROOT / rel, 8)


def test_pyproject_if_present_is_parseable():
    path = REPO_ROOT / "pyproject.toml"
    if path.exists():
        try:
            import tomllib
        except ModuleNotFoundError:
            return
        tomllib.loads(path.read_text(encoding="utf-8"))


def test_bootstrap_and_validator_scripts_are_importable():
    for path in [
        REPO_ROOT / "scripts" / "bootstrap_cursor_research_workspace.py",
        TEMPLATE_ROOT / "scripts" / "validate_workspace.py",
    ]:
        spec = importlib.util.spec_from_file_location(path.stem, path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
