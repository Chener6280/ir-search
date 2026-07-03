from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any


EXPECTED_TOOLS = {
    "search",
    "fetch_document",
    "extract_evidence",
    "verify_claims",
    "deep_research",
    "source_health",
}


def run_diagnostics(
    *,
    ir_search_python: Path,
    ir_search_path: Path,
    live: str = "0",
    timeout_sec: int = 10,
    env_local_path: Path | None = None,
) -> dict[str, Any]:
    python_path = ir_search_python.expanduser()
    repo_path = ir_search_path.expanduser()
    env_local = _resolve_env_local(env_local_path)
    env_local_values = _load_env_file_values(env_local) if env_local and env_local.exists() else {}
    initial_env = dict(os.environ)
    initial_env.update(env_local_values)
    initial_env["IR_SEARCH_LIVE"] = env_local_values.get("IR_SEARCH_LIVE", live)
    result: dict[str, Any] = {
        "ok": False,
        "selected_python": str(python_path),
        "ir_search_path": str(repo_path),
        "env_local_path": str(env_local) if env_local else "",
        "env_local_loaded": bool(env_local and env_local.exists()),
        "checks": {
            "python_exists": python_path.exists(),
            "python_executable": python_path.exists() and _is_executable(python_path),
            "ir_search_path_exists": repo_path.exists(),
            "import_ir_search": False,
            "import_fastmcp": False,
            "import_mcp_server": False,
            "tool_list": False,
        },
        "tool_names": [],
        "env": _env_presence(initial_env),
        "source_diagnostics": {},
        "errors": [],
        "warnings": [],
        "fix": _fix_commands(python_path, repo_path),
    }

    if not result["checks"]["python_exists"]:
        result["errors"].append(f"IR_SEARCH_PYTHON does not exist: {python_path}")
        return result
    if not result["checks"]["python_executable"]:
        result["errors"].append(f"IR_SEARCH_PYTHON is not executable: {python_path}")
        return result
    if not result["checks"]["ir_search_path_exists"]:
        result["errors"].append(f"IR_SEARCH_PATH does not exist: {repo_path}")
        return result

    probes = [
        ("import_ir_search", "import ir_search; print('ir_search ok')"),
        ("import_fastmcp", "from mcp.server.fastmcp import FastMCP; print('mcp ok')"),
        ("import_mcp_server", "import ir_search.mcp_server; print('mcp_server ok')"),
    ]
    for check_name, code in probes:
        completed = _run_probe(python_path, repo_path, code, live=live, timeout_sec=timeout_sec, env_overrides=env_local_values)
        if completed.returncode == 0:
            result["checks"][check_name] = True
        else:
            result["errors"].append(_probe_error_message(check_name, completed, python_path, repo_path))
            return result

    tool_probe = _run_probe(
        python_path,
        repo_path,
        "import json; from ir_search.mcp_server import list_tool_names; print(json.dumps(list_tool_names()))",
        live=live,
        timeout_sec=timeout_sec,
        env_overrides=env_local_values,
    )
    if tool_probe.returncode != 0:
        result["errors"].append(_probe_error_message("tool_list", tool_probe, python_path, repo_path))
        return result
    try:
        tool_names = set(json.loads(tool_probe.stdout.strip() or "[]"))
    except json.JSONDecodeError:
        result["errors"].append(f"Could not parse MCP tool list from selected Python: {tool_probe.stdout!r}")
        return result
    result["tool_names"] = sorted(tool_names)
    if tool_names != EXPECTED_TOOLS:
        result["errors"].append(f"MCP tool list mismatch: expected {sorted(EXPECTED_TOOLS)}, got {sorted(tool_names)}")
        return result

    result["checks"]["tool_list"] = True
    result["ok"] = True
    _attach_source_health_diagnostics(result, python_path, repo_path, live=live, timeout_sec=timeout_sec, env_overrides=env_local_values)
    return result


def print_human_report(diagnostics: dict[str, Any]) -> None:
    python_path = diagnostics["selected_python"]
    ir_search_path = diagnostics["ir_search_path"]
    checks = diagnostics["checks"]

    _print_check(checks["python_exists"], f"Python exists: {python_path}")
    _print_check(checks["python_executable"], f"Python executable: {python_path}")
    _print_check(checks["ir_search_path_exists"], f"IR_SEARCH_PATH: {ir_search_path}")
    _print_check(checks["import_ir_search"], "import ir_search")
    _print_check(checks["import_fastmcp"], "import mcp.server.fastmcp.FastMCP")
    _print_check(checks["import_mcp_server"], "import ir_search.mcp_server")
    tool_names = diagnostics.get("tool_names") or []
    tool_message = "MCP tools: " + (", ".join(tool_names) if tool_names else "not available")
    _print_check(checks["tool_list"], tool_message)
    print(f"[INFO] .env.local: {diagnostics.get('env_local_path') or 'not configured'} ({'loaded' if diagnostics.get('env_local_loaded') else 'not loaded'})")
    for key, value in diagnostics.get("env", {}).items():
        print(f"[INFO] {key}={str(value).lower() if isinstance(value, bool) else value}")

    if diagnostics["ok"]:
        print("[OK] ir_search MCP runtime preflight passed.")
        return

    print()
    print("[ERROR] ir_search MCP runtime preflight failed.")
    for error in diagnostics["errors"]:
        print(error)
    print()
    print(_fix_message(Path(python_path), Path(ir_search_path)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnose the Python runtime used by the ir_search Cursor MCP server")
    parser.add_argument("--ir-search-python", required=True, type=Path)
    parser.add_argument("--ir-search-path", required=True, type=Path)
    parser.add_argument("--live", choices=["0", "1"], default="0")
    parser.add_argument("--env-local", type=Path, help="Optional .env.local file to include in the diagnostic subprocess")
    parser.add_argument("--json", action="store_true", help="Print machine-readable diagnostics")
    parser.add_argument("--timeout-sec", type=int, default=10)
    args = parser.parse_args(argv)

    diagnostics = run_diagnostics(
        ir_search_python=args.ir_search_python,
        ir_search_path=args.ir_search_path,
        live=args.live,
        timeout_sec=args.timeout_sec,
        env_local_path=args.env_local,
    )
    if args.json:
        print(json.dumps(diagnostics, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_human_report(diagnostics)
    return 0 if diagnostics["ok"] else 1


def _run_probe(
    python_path: Path,
    ir_search_path: Path,
    code: str,
    *,
    live: str,
    timeout_sec: int,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    env["PYTHONPATH"] = str(ir_search_path) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["IR_SEARCH_PATH"] = str(ir_search_path)
    env["IR_SEARCH_LIVE"] = env_overrides.get("IR_SEARCH_LIVE", live) if env_overrides else live
    try:
        return subprocess.run(
            [str(python_path), "-c", code],
            text=True,
            capture_output=True,
            env=env,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(args=exc.cmd or [str(python_path), "-c", code], returncode=124, stdout=exc.stdout or "", stderr=exc.stderr or "Timed out")


def _probe_error_message(check_name: str, completed: subprocess.CompletedProcess[str], python_path: Path, ir_search_path: Path) -> str:
    if check_name == "import_fastmcp":
        return (
            "Selected IR_SEARCH_PYTHON cannot import mcp.server.fastmcp.FastMCP."
            + _stderr_excerpt(completed)
        )
    return f"Probe failed for {check_name} with exit code {completed.returncode}.{_stderr_excerpt(completed)}"


def _stderr_excerpt(completed: subprocess.CompletedProcess[str]) -> str:
    stderr = (completed.stderr or "").strip()
    if not stderr:
        return ""
    return "\n\nProbe stderr:\n" + stderr[-1200:]


def _fix_message(python_path: Path, ir_search_path: Path) -> str:
    return (
        "Selected Python:\n"
        f"  {python_path}\n\n"
        "Fix option A: install MCP extra into this Python:\n"
        f"  {python_path} -m pip install -e \"{ir_search_path}[mcp]\"\n\n"
        "Note: the MCP extra requires Python 3.10+ because mcp>=1 requires Python >=3.10.\n\n"
        "Fix option B: create/use an ir-search venv with Python 3.10+ and rerun bootstrap:\n"
        f"  python3.12 -m venv \"{ir_search_path}/.venv\"  # or another Python 3.10+\n"
        f"  \"{ir_search_path}/.venv/bin/python\" -m pip install -e \"{ir_search_path}[mcp]\"\n"
        "  python scripts/bootstrap_cursor_research_workspace.py \\\n"
        "    --target <workspace> \\\n"
        f"    --ir-search-python \"{ir_search_path}/.venv/bin/python\" \\\n"
        f"    --ir-search-path \"{ir_search_path}\" \\\n"
        "    --overwrite"
    )


def _fix_commands(python_path: Path, ir_search_path: Path) -> dict[str, str]:
    return {
        "install": f'{python_path} -m pip install -e "{ir_search_path}[mcp]"',
        "venv_install": f'python3.12 -m venv "{ir_search_path}/.venv" && "{ir_search_path}/.venv/bin/python" -m pip install -e "{ir_search_path}[mcp]"',
        "rerun_bootstrap": (
            "python scripts/bootstrap_cursor_research_workspace.py "
            f'--target <workspace> --ir-search-python "{ir_search_path}/.venv/bin/python" '
            f'--ir-search-path "{ir_search_path}" --overwrite'
        ),
    }


def _attach_source_health_diagnostics(
    result: dict[str, Any],
    python_path: Path,
    ir_search_path: Path,
    *,
    live: str,
    timeout_sec: int,
    env_overrides: dict[str, str],
) -> None:
    probe = _run_probe(
        python_path,
        ir_search_path,
        (
            "import json; "
            "from ir_search.source_health import source_health; "
            "payload=source_health(); "
            "print(json.dumps({'env': payload.get('env', {}), 'sources': {k: {'ok': v.get('ok'), 'adapter_mode': v.get('adapter_mode'), 'availability_reason': v.get('availability_reason'), 'diagnostics': v.get('diagnostics', {})} for k, v in payload.get('sources', {}).items()}}, ensure_ascii=False))"
        ),
        live=live,
        timeout_sec=timeout_sec,
        env_overrides=env_overrides,
    )
    if probe.returncode != 0:
        result["warnings"].append(_probe_error_message("source_health", probe, python_path, ir_search_path))
        return
    if not probe.stdout.strip():
        result["warnings"].append("source_health probe returned no output")
        return
    try:
        payload = json.loads(probe.stdout)
    except json.JSONDecodeError:
        result["warnings"].append("source_health probe returned non-JSON output")
        return
    result["env"] = payload.get("env", result["env"])
    result["source_diagnostics"] = payload.get("sources", {})


def _env_presence(env: dict[str, str]) -> dict[str, bool | str]:
    return {
        "IR_SEARCH_LIVE": env.get("IR_SEARCH_LIVE", "0"),
        "has_BOCHA_API_KEY": bool(env.get("BOCHA_API_KEY")),
        "has_EXA_API_KEY": bool(env.get("EXA_API_KEY")),
        "has_TAVILY_API_KEY": bool(env.get("TAVILY_API_KEY")),
        "has_ANYSEARCH_API_KEY": bool(env.get("ANYSEARCH_API_KEY")),
        "has_DAJIALA_KEY": bool(env.get("DAJIALA_KEY")),
        "has_ZSXQ_GROUP_IDS": bool(env.get("ZSXQ_GROUP_IDS")),
        "has_WECHAT_OPENCLI_COMMAND": bool(env.get("WECHAT_OPENCLI_COMMAND")),
        "has_MANUAL_WECHAT_ROOT": bool(env.get("MANUAL_WECHAT_ROOT") or env.get("IR_SEARCH_MANUAL_WECHAT_ROOT")),
    }


def _resolve_env_local(env_local_path: Path | None) -> Path | None:
    if env_local_path is not None:
        return env_local_path.expanduser()
    cwd_candidate = Path.cwd() / ".env.local"
    if cwd_candidate.exists():
        return cwd_candidate
    script_candidate = Path(__file__).resolve().parents[1] / ".env.local"
    if script_candidate.exists():
        return script_candidate
    return cwd_candidate


def _load_env_file_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].strip()
        key, value = stripped.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = value.strip().strip("\"'")
    return values


def _print_check(ok: bool, message: str) -> None:
    print(("[OK] " if ok else "[ERROR] ") + message)


def _is_executable(path: Path) -> bool:
    try:
        mode = path.stat().st_mode
    except OSError:
        return False
    return bool(mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))


if __name__ == "__main__":
    raise SystemExit(main())
