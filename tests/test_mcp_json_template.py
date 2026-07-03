from __future__ import annotations

import json
from pathlib import Path

from scripts.bootstrap_cursor_research_workspace import main as bootstrap_main


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = REPO_ROOT / "templates" / "cursor-research-workspace"


def test_mcp_json_template_declares_stdio_server():
    mcp = json.loads((TEMPLATE_ROOT / ".cursor" / "mcp.json.template").read_text(encoding="utf-8"))
    server = mcp["mcpServers"]["ir_search"]

    assert server["type"] == "stdio"
    assert server["command"] == "/bin/zsh"
    assert server["args"] == ["{{WORKSPACE_ROOT}}/scripts/run_ir_search_mcp.sh"]


def test_bootstrap_output_mcp_json_declares_stdio_server(tmp_path):
    target = tmp_path / "research"
    python_path, ir_search_path = _fake_runtime(tmp_path)

    assert bootstrap_main(["--target", str(target), "--ir-search-python", str(python_path), "--ir-search-path", str(ir_search_path)]) == 0
    mcp = json.loads((target / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    server = mcp["mcpServers"]["ir_search"]

    assert server["type"] == "stdio"
    assert server["command"] == "/bin/zsh"
    assert server["args"] == [str(target / "scripts" / "run_ir_search_mcp.sh")]


def _fake_runtime(tmp_path: Path) -> tuple[Path, Path]:
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
