from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER_TEMPLATE = REPO_ROOT / "templates" / "cursor-research-workspace" / "scripts" / "run_ir_search_mcp.sh"


def test_run_ir_search_mcp_loads_env_local_without_printing_secret(tmp_path):
    workspace = tmp_path / "workspace"
    scripts = workspace / "scripts"
    scripts.mkdir(parents=True)
    runner = scripts / "run_ir_search_mcp.sh"
    shutil.copy2(RUNNER_TEMPLATE, runner)
    runner.chmod(0o755)
    capture = tmp_path / "capture.txt"
    fake_python = tmp_path / "fake-python"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"${1:-}\" == \"-c\" ]]; then\n"
        "  exit 0\n"
        "fi\n"
        "printf 'live=%s\\nbocha=%s\\n' \"${IR_SEARCH_LIVE:-}\" \"${BOCHA_API_KEY:-}\" > \"${CAPTURE_FILE}\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    (workspace / ".env.local").write_text("IR_SEARCH_LIVE=1\nBOCHA_API_KEY=bocha_test_value\n", encoding="utf-8")

    env = os.environ.copy()
    env.update({
        "CAPTURE_FILE": str(capture),
        "IR_SEARCH_PYTHON": str(fake_python),
        "IR_SEARCH_PATH": str(tmp_path / "fake-ir-search"),
    })
    completed = subprocess.run(["/bin/zsh", str(runner)], env=env, text=True, capture_output=True, check=False)

    assert completed.returncode == 0
    assert capture.read_text(encoding="utf-8") == "live=1\nbocha=bocha_test_value\n"
    assert "bocha_test_value" not in completed.stdout
    assert "bocha_test_value" not in completed.stderr
