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
    parser.add_argument("--ir-search-live", default="0")
    parser.add_argument("--manual-wechat-root", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
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
    if str(args.ir_search_live) == "1" and not _has_live_provider_key(args.env_file):
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
    result = validate_generated_workspace(target)
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


def _has_live_provider_key(env_file: Path | None = None) -> bool:
    provider_keys = ["BOCHA_API_KEY", "EXA_API_KEY", "TAVILY_API_KEY", "ANYSEARCH_API_KEY"]
    if any(os.environ.get(name) for name in provider_keys):
        return True
    if env_file is None:
        return False
    path = _absolute_path(env_file)
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    return any(_env_file_defines_key(text, name) for name in provider_keys)


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
    print("2. Run prompt: prompts/R-SOURCE-HEALTH.md")
    print("3. If all live sources are unavailable, check env expansion and API keys.")
    print("4. To enable live mode, rerun with --ir-search-live 1 and optionally --env-file /path/to/ir_search.env.")


def validate_generated_workspace(target: Path) -> int:
    validator_path = target / "scripts" / "validate_workspace.py"
    spec = importlib.util.spec_from_file_location("cursor_workspace_validator", validator_path)
    if spec is None or spec.loader is None:
        print(f"[ERROR] Could not load validator: {validator_path}")
        return 1
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return int(module.main([str(target), "--mode", "generated"]))


if __name__ == "__main__":
    raise SystemExit(main())
