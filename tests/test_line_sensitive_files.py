from __future__ import annotations

from pathlib import Path
import importlib.util


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = REPO_ROOT / "templates" / "cursor-research-workspace"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_root_readme_is_multiline_ir_search_readme():
    text = _text(REPO_ROOT / "README.md")

    assert text.count("\n") > 10
    assert "ir-search" in text
    assert "MCP" in text


def test_mdc_frontmatter_is_line_delimited():
    for path in sorted((TEMPLATE_ROOT / ".cursor" / "rules").glob("*.mdc")):
        lines = _text(path).splitlines()
        assert lines[0] == "---", path
        assert "---" in lines[1:], path
        assert any(line.startswith("description:") for line in lines[1:]), path
        assert any(line.startswith("alwaysApply:") for line in lines[1:]), path


def test_ignore_files_have_active_multiline_rules():
    for rel in [".cursorignore", ".cursorindexingignore"]:
        text = _text(TEMPLATE_ROOT / rel)
        active = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]

        assert text.count("\n") > 8
        assert active
        assert active[0] == "/*"

    assert _text(TEMPLATE_ROOT / ".cursorindexingignore").count("\n") >= 25
    assert _text(TEMPLATE_ROOT / ".cursor" / "mcp.json.template").count("\n") >= 8


def test_cursorindexingignore_active_rules_are_complete():
    text = _text(TEMPLATE_ROOT / ".cursorindexingignore")
    active = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

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


def test_gitattributes_is_line_delimited():
    text = _text(REPO_ROOT / ".gitattributes")

    assert text.count("\n") >= 12
    for rule in [
        "* text=auto eol=lf",
        "*.py text eol=lf",
        "*.mdc text eol=lf",
        "*.yaml text eol=lf",
        "*.gitignore text eol=lf",
        ".cursorignore text eol=lf",
        ".cursorindexingignore text eol=lf",
    ]:
        assert rule in text.splitlines()


def test_python_files_do_not_use_cr_only_newlines():
    for path in list((REPO_ROOT / "scripts").glob("*.py")) + list((TEMPLATE_ROOT / "scripts").glob("*.py")):
        data = path.read_bytes()

        assert b"\r" not in data
        assert data.count(b"\n") > 10


def test_key_scripts_have_reviewable_line_counts():
    thresholds = {
        REPO_ROOT / "scripts" / "bootstrap_cursor_research_workspace.py": 80,
        REPO_ROOT / "scripts" / "run_acceptance_cases.py": 80,
        REPO_ROOT / "scripts" / "score_acceptance_results.py": 120,
        TEMPLATE_ROOT / "scripts" / "validate_workspace.py": 120,
    }
    for path, minimum in thresholds.items():
        assert _text(path).count("\n") >= minimum, path


def test_pyproject_if_present_is_parseable():
    path = REPO_ROOT / "pyproject.toml"
    if path.exists():
        try:
            import tomllib
        except ModuleNotFoundError:
            return
        tomllib.loads(path.read_text(encoding="utf-8"))


def test_acceptance_cases_are_multiline_and_cover_twenty_cases():
    path = REPO_ROOT / "tests" / "acceptance_cases.yaml"
    text = _text(path)

    assert text.count("\n") > 120
    assert text.count("  - id:") >= 20
    assert "must_not:" in text


def test_bootstrap_and_validator_scripts_are_importable():
    for path in [
        REPO_ROOT / "scripts" / "bootstrap_cursor_research_workspace.py",
        TEMPLATE_ROOT / "scripts" / "validate_workspace.py",
    ]:
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
