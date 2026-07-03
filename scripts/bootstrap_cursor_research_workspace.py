from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = REPO_ROOT / "templates" / "cursor-research-workspace"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bootstrap a Cursor research workspace from the ir-search template")
    parser.add_argument("--target", required=True, type=Path, help="Target research workspace directory")
    parser.add_argument("--ir-search-python", required=True, type=Path, help="Absolute path to the ir-search environment Python")
    parser.add_argument("--ir-search-path", type=Path, help="Absolute path to the ir-search repository/package root")
    parser.add_argument("--env-file", type=Path, help="Optional env file sourced by the MCP wrapper before startup")
    parser.add_argument("--env-local-path", type=Path, help="Optional local env file to symlink as target .env.local")
    parser.add_argument("--ir-search-live", default="0")
    parser.add_argument("--manual-wechat-root", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-mcp-runtime-check", action="store_true", help="Skip the ir_search MCP runtime preflight")
    args = parser.parse_args(argv)

    target = args.target.expanduser().resolve()
    ir_search_path = _resolve_ir_search_path(args.ir_search_path)
    manual_wechat_root = (args.manual_wechat_root.expanduser().resolve() if args.manual_wechat_root else target / "sources" / "manual_wechat_articles")
    cache_dir = (args.cache_dir.expanduser().resolve() if args.cache_dir else target / ".ir_search_cache")
    replacements = {
        "{{WORKSPACE_ROOT}}": str(target),
        "{{IR_SEARCH_PYTHON}}": str(_absolute_path(args.ir_search_python)),
        "{{IR_SEARCH_PATH}}": str(ir_search_path),
        "{{IR_SEARCH_ENV_FILE}}": str(_absolute_path(args.env_file)) if args.env_file else "",
        "{{IR_SEARCH_LIVE}}": str(args.ir_search_live),
        "{{MANUAL_WECHAT_ROOT}}": str(manual_wechat_root),
        "{{IR_SEARCH_CACHE_DIR}}": str(cache_dir),
    }
    if str(args.ir_search_live) == "1" and not _has_live_provider_key(args.env_file, args.env_local_path):
        print(
            "[WARN] IR_SEARCH_LIVE=1 but no BOCHA_API_KEY / EXA_API_KEY / "
            "TAVILY_API_KEY / ANYSEARCH_API_KEY was detected in the current shell. "
            "Cursor may still expand ${env:KEY}, but please confirm with R-SOURCE-HEALTH."
        )

    planned = plan_files(target)
    if args.dry_run:
        print(f"[DRY-RUN] Template: {TEMPLATE_ROOT}")
        print(f"[DRY-RUN] Target: {target}")
        for src, dst in planned:
            print(f"[DRY-RUN] copy {src.relative_to(TEMPLATE_ROOT)} -> {dst}")
        print("[DRY-RUN] render .cursor/mcp.json.example")
        print("[DRY-RUN] create .cursor/mcp.json if missing")
        if args.env_local_path:
            print("[DRY-RUN] link .env.local from --env-local-path")
        if args.skip_mcp_runtime_check:
            print("[DRY-RUN] skip MCP runtime preflight")
        else:
            print("[DRY-RUN] run MCP runtime preflight")
        return 0

    target.mkdir(parents=True, exist_ok=True)
    manual_wechat_root.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    for src, dst in planned:
        if dst.exists() and not args.overwrite:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    render_mcp_files(target, replacements, overwrite=args.overwrite)
    if args.env_local_path:
        link_env_local(target, _absolute_path(args.env_local_path), overwrite=args.overwrite)
    if args.skip_mcp_runtime_check:
        print("[WARN] MCP runtime check skipped. Cursor may not show the ir_search MCP server until you run scripts/doctor_ir_search_mcp.py.")
    else:
        preflight_result = run_mcp_runtime_preflight(
            ir_search_python=_absolute_path(args.ir_search_python),
            ir_search_path=ir_search_path,
            live=str(args.ir_search_live),
            env_local_path=target / ".env.local",
        )
        if preflight_result != 0:
            print("[ERROR] Workspace files were generated, but the ir_search MCP runtime is invalid. Fix the Python runtime and rerun bootstrap.")
            return preflight_result
    result = validate_generated_workspace(target, skip_mcp_runtime_check=args.skip_mcp_runtime_check)
    if result == 0:
        print_onboarding(target)
    return result


def plan_files(target: Path) -> list[tuple[Path, Path]]:
    files: list[tuple[Path, Path]] = []
    for src in sorted(TEMPLATE_ROOT.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(TEMPLATE_ROOT)
        files.append((src, target / rel))
    return files


def render_mcp_files(target: Path, replacements: dict[str, str], *, overwrite: bool) -> None:
    template_path = target / ".cursor" / "mcp.json.template"
    rendered = template_path.read_text(encoding="utf-8")
    for needle, value in replacements.items():
        rendered = rendered.replace(needle, value)
    example_path = target / ".cursor" / "mcp.json.example"
    mcp_path = target / ".cursor" / "mcp.json"
    if overwrite or not example_path.exists():
        example_path.write_text(rendered, encoding="utf-8")
    if overwrite or not mcp_path.exists():
        mcp_path.write_text(rendered, encoding="utf-8")


def link_env_local(target: Path, source: Path, *, overwrite: bool) -> None:
    if not source.exists():
        raise SystemExit(f"[ERROR] --env-local-path does not exist: {source}")
    destination = target / ".env.local"
    if destination.exists() or destination.is_symlink():
        if not overwrite:
            return
        destination.unlink()
    destination.symlink_to(source)


def _resolve_ir_search_path(arg_path: Path | None) -> Path:
    if arg_path is not None:
        return _absolute_path(arg_path)
    if (REPO_ROOT / "ir_search" / "__init__.py").exists():
        return REPO_ROOT.resolve()
    raise SystemExit("[ERROR] --ir-search-path is required when bootstrapping outside the ir-search repository")


def _absolute_path(path: Path) -> Path:
    path = path.expanduser()
    if path.is_absolute():
        return path
    return (Path.cwd() / path).absolute()


def _has_live_provider_key(*paths: Path | None) -> bool:
    provider_keys = ["BOCHA_API_KEY", "EXA_API_KEY", "TAVILY_API_KEY", "ANYSEARCH_API_KEY"]
    if any(os.environ.get(name) for name in provider_keys):
        return True
    for env_file in paths:
        if env_file is None:
            continue
        path = _absolute_path(env_file)
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if any(_env_file_defines_key(text, name) for name in provider_keys):
            return True
    return False


def _env_file_defines_key(text: str, name: str) -> bool:
    prefix = f"{name}="
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].strip()
        if stripped.startswith(prefix) and stripped != prefix:
            return True
    return False


def print_onboarding(target: Path) -> None:
    print("[OK] Workspace created.")
    print("Next steps:")
    print(f"1. Open this folder alone in Cursor: {target}")
    print("2. If Cursor MCP is not green, run: python scripts/doctor_ir_search_mcp.py --ir-search-python <python> --ir-search-path <ir-search>")
    print("3. For local keys, run: cp .env.local.example .env.local, fill values locally, then Reload Window / start a new Agent chat.")
    print("4. Run prompt: prompts/R-SOURCE-HEALTH.md")
    print("5. If all live sources are unavailable, check .env.local, env expansion, and API keys.")
    print("6. To enable live mode, set IR_SEARCH_LIVE=1 in .env.local or rerun with --ir-search-live 1.")


def run_mcp_runtime_preflight(*, ir_search_python: Path, ir_search_path: Path, live: str, env_local_path: Path | None = None) -> int:
    doctor_path = REPO_ROOT / "scripts" / "doctor_ir_search_mcp.py"
    spec = importlib.util.spec_from_file_location("doctor_ir_search_mcp", doctor_path)
    if spec is None or spec.loader is None:
        print(f"[ERROR] Could not load MCP runtime doctor: {doctor_path}")
        return 1
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    diagnostics = module.run_diagnostics(ir_search_python=ir_search_python, ir_search_path=ir_search_path, live=live, env_local_path=env_local_path)
    module.print_human_report(diagnostics)
    return 0 if diagnostics["ok"] else 1


def validate_generated_workspace(target: Path, *, skip_mcp_runtime_check: bool = False) -> int:
    validator_path = target / "scripts" / "validate_workspace.py"
    spec = importlib.util.spec_from_file_location("cursor_workspace_validator", validator_path)
    if spec is None or spec.loader is None:
        print(f"[ERROR] Could not load validator: {validator_path}")
        return 1
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    argv = [str(target), "--mode", "generated"]
    if skip_mcp_runtime_check:
        argv.append("--skip-mcp-runtime-check")
    return int(module.main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
