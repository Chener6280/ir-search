from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = REPO_ROOT / "templates" / "cursor-research-workspace"


def test_ir_search_readme_links_template_bootstrap():
    root_readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    assert "Cursor research workspace template" in root_readme
    assert "bootstrap" in root_readme
    assert "docs/cursor_research_workspace_setup.md" in root_readme


def test_root_readme_is_not_generated_workspace_readme():
    root_readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    template_readme = (TEMPLATE_ROOT / "README.md").read_text(encoding="utf-8")

    assert root_readme != template_readme
    assert "non-coding research environment" in template_readme


def test_agents_roles_are_separated():
    root_agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    template_agents = (TEMPLATE_ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert (
        "deterministic investment-research search and evidence engine" in root_agents
        or "Template Repository Instructions" in root_agents
    )
    assert root_agents != template_agents
    assert "This is a non-coding research workspace" in template_agents
