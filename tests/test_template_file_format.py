from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = REPO_ROOT / "templates" / "cursor-research-workspace"


def test_all_mdc_frontmatter_valid():
    for path in (TEMPLATE_ROOT / ".cursor" / "rules").glob("*.mdc"):
        lines = path.read_text(encoding="utf-8").splitlines()

        assert lines[0] == "---", path
        assert "---" in lines[1:], path
        end = lines[1:].index("---") + 1
        frontmatter = {}
        for line in lines[1:end]:
            key, value = line.split(":", 1)
            frontmatter[key.strip()] = value.strip()

        assert frontmatter.get("description"), path
        assert frontmatter.get("alwaysApply") in {"true", "false"}, path


def test_ignore_files_are_effective_line_based_files():
    for rel in [".cursorignore", ".cursorindexingignore"]:
        path = TEMPLATE_ROOT / rel
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#")]

        assert lines
        assert lines[0] == "/*"


def test_mcp_json_template_is_valid_multiline_json():
    path = TEMPLATE_ROOT / ".cursor" / "mcp.json.template"
    text = path.read_text(encoding="utf-8")

    assert text.count("\n") > 2
    mcp = json.loads(text)["mcpServers"]["ir_search"]
    assert mcp["command"] == "/bin/zsh"
    assert mcp["env"]["IR_SEARCH_PYTHON"] == "{{IR_SEARCH_PYTHON}}"
    assert mcp["env"]["IR_SEARCH_ENV_FILE"] == "{{IR_SEARCH_ENV_FILE}}"


def test_line_sensitive_template_files_are_not_single_line():
    paths = [
        TEMPLATE_ROOT / "README.md",
        TEMPLATE_ROOT / "AGENTS.md",
        TEMPLATE_ROOT / ".cursorignore",
        TEMPLATE_ROOT / ".cursorindexingignore",
        TEMPLATE_ROOT / ".cursor" / "mcp.json.template",
    ]
    paths.extend((TEMPLATE_ROOT / ".cursor" / "rules").glob("*.mdc"))
    paths.extend((TEMPLATE_ROOT / "prompts").glob("*.md"))

    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert text.count("\n") > 1, path
