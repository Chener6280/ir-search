from __future__ import annotations

from pathlib import Path


TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "templates" / "cursor-research-workspace"


def _active_rules(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#")]


def test_cursorignore_default_denies_and_allows_research_workspace_files():
    rules = _active_rules(TEMPLATE_ROOT / ".cursorignore")

    assert rules[0] == "/*"
    for rule in ["!/.cursor/", "!/docs/", "!/prompts/", "!/notes/", "!/sources/", "!/outputs/", "!/scripts/"]:
        assert rule in rules


def test_cursorindexingignore_indexes_outputs_and_scripts_but_excludes_raw_tmp():
    rules = _active_rules(TEMPLATE_ROOT / ".cursorindexingignore")

    assert rules[0] == "/*"
    for rule in [
        "!/docs/",
        "!/docs/**",
        "!/outputs/reports/",
        "!/outputs/reports/**",
        "!/outputs/memos/",
        "!/outputs/memos/**",
        "!/outputs/source_tables/",
        "!/outputs/source_tables/**",
        "!/scripts/",
        "!/scripts/**",
    ]:
        assert rule in rules
    for rule in ["/outputs/raw/", "/outputs/raw/**", "/outputs/tmp/", "/outputs/tmp/**"]:
        assert rule in rules


def test_output_directory_strategy_is_documented():
    readme = (TEMPLATE_ROOT / "README.md").read_text(encoding="utf-8")

    for phrase in ["outputs/reports/", "outputs/memos/", "outputs/source_tables/", "outputs/raw/", "outputs/tmp/"]:
        assert phrase in readme
