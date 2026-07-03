from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


REQUIRED_FILES = [
    "AGENTS.md",
    "README.md",
    ".cursorignore",
    ".cursorindexingignore",
    ".cursor/mcp.json.template",
    ".cursor/rules/00-research-defaults.mdc",
    ".cursor/rules/10-finance-research-style.mdc",
    ".cursor/rules/20-ir-search-evidence-policy.mdc",
    ".cursor/rules/30-output-format.mdc",
    ".cursor/rules/40-fallback-policy.mdc",
    ".cursor/rules/50-current-facts-policy.mdc",
    "docs/onboarding.md",
    "prompts/README.md",
    "prompts/R-FINANCE-WEB.md",
    "prompts/R-LITERATURE.md",
    "prompts/R-LATEST-GUARD.md",
    "prompts/R-VERIFY-CLAIMS.md",
    "prompts/R-SOURCE-HEALTH.md",
    "prompts/R-DEEP-RESEARCH-SMOKE.md",
    "notes/smoke_test_checklist.md",
    "scripts/bootstrap_workspace.sh",
    "scripts/run_ir_search_mcp.sh",
    "sources/papers/sample_monetary_policy_transmission.md",
]

UNREPLACED_PLACEHOLDERS = [
    "{{IR_SEARCH_PYTHON}}",
    "{{IR_SEARCH_LIVE}}",
    "{{MANUAL_WECHAT_ROOT}}",
    "{{IR_SEARCH_CACHE_DIR}}",
]

REQUIRED_RULE_PHRASES = [
    "source_health",
    "deep_research",
    "search snippets",
    "untrusted",
    "fallback",
    "mock",
    "placeholder",
    "insufficient_evidence",
    "contradicted",
]

STATUS_VALUES = ["supported", "mixed", "insufficient_evidence", "contradicted"]
SKIP_SCAN_PARTS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ir_search_cache"}
ENV_REF_RE = re.compile(r"\$\{env:[A-Za-z_][A-Za-z0-9_]*\}")
PERSONAL_PATH_PATTERNS = [
    re.compile(r"/Users/(?!example\b|Shared\b)[A-Za-z0-9._-]+"),
    re.compile(r"C:\\Users\\(?!example\\)[A-Za-z0-9._-]+"),
    re.compile(r"/home/(?!example\b)[A-Za-z0-9._-]+"),
]
SECRET_FIELD_RE = re.compile(
    r"(?i)\b(api[_-]?key|key|token|secret|cookie|authorization|bearer|access[_-]?token|refresh[_-]?token|session)\b"
    r"\s*[:=]\s*[\"']?([^\"',\s#]+)"
)
VALIDATOR_VERSION = "2026-07-03-r5"


def validate_workspace(root: Path, *, strict: bool = False, mode: str = "generated") -> list[str]:
    errors, _warnings = collect_validation_issues(root, strict=strict, mode=mode)
    return errors


def collect_validation_issues(root: Path, *, strict: bool = False, mode: str = "generated") -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    root = root.expanduser().resolve()
    if not root.exists():
        return [f"Workspace does not exist: {root}"], warnings
    if mode not in {"template", "generated"}:
        return [f"Invalid validation mode: {mode}"], warnings

    for rel in REQUIRED_FILES:
        if not (root / rel).exists():
            errors.append(f"Missing file: {rel}")

    _validate_mcp(root, errors, warnings, mode=mode)
    _validate_rules(root, errors)
    _validate_ignore_files(root, errors)
    _validate_line_sensitive_files(root, errors)
    _validate_policy_consistency(root, errors)
    _scan_workspace_text(root, errors, warnings, strict=strict)
    return errors, warnings


def _validate_mcp(root: Path, errors: list[str], warnings: list[str], *, mode: str) -> None:
    if not (root / ".cursor/mcp.json").exists():
        if mode == "generated":
            errors.append("Missing .cursor/mcp.json in generated workspace")
        else:
            warnings.append("Missing .cursor/mcp.json; bootstrap renders this file for generated workspaces.")
    for rel in [".cursor/mcp.json.template", ".cursor/mcp.json"]:
        path = root / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if text.count("\n") < 2:
            errors.append(f"{rel} must be formatted as multi-line JSON")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            errors.append(f"Invalid JSON: {rel}: {exc}")
            data = {}
        if rel == ".cursor/mcp.json":
            if "/ABSOLUTE/PATH/TO/" in text:
                errors.append(".cursor/mcp.json contains unreplaced /ABSOLUTE/PATH/TO placeholder")
            for placeholder in UNREPLACED_PLACEHOLDERS:
                if placeholder in text:
                    errors.append(f"Unreplaced placeholder in .cursor/mcp.json: {placeholder}")
            if "mcpServers" not in data:
                errors.append("Missing mcpServers in .cursor/mcp.json")
            _validate_ir_search_mcp_config(root, data, errors)
        if ENV_REF_RE.search(text):
            warnings.append(f"{rel} uses ${'{'}env:KEY{'}'} references; confirm Cursor env expansion or launch Cursor from an exported shell.")


def _validate_ir_search_mcp_config(root: Path, data: dict, errors: list[str]) -> None:
    server = data.get("mcpServers", {}).get("ir_search", {}) if isinstance(data, dict) else {}
    command = server.get("command", "")
    if not command:
        errors.append("Missing ir_search MCP command")
    elif command.startswith("/ABSOLUTE/PATH/TO/"):
        errors.append("ir_search MCP command still uses /ABSOLUTE/PATH/TO placeholder")
    elif command.startswith("/") and not Path(command).exists():
        errors.append(f"ir_search MCP command does not exist: {command}")

    for arg in server.get("args", []):
        if isinstance(arg, str) and arg.startswith("/ABSOLUTE/PATH/TO/"):
            errors.append(f"ir_search MCP arg still uses /ABSOLUTE/PATH/TO placeholder: {arg}")
        elif isinstance(arg, str) and arg.startswith("/") and arg.endswith(".sh") and not Path(arg).exists():
            errors.append(f"ir_search MCP script arg does not exist: {arg}")

    env = server.get("env", {})
    python_path = env.get("IR_SEARCH_PYTHON", "")
    if python_path.startswith("/ABSOLUTE/PATH/TO/"):
        errors.append("IR_SEARCH_PYTHON still uses /ABSOLUTE/PATH/TO placeholder")
    elif python_path and not Path(python_path).exists():
        errors.append(f"IR_SEARCH_PYTHON does not exist: {python_path}")

    ir_search_path = env.get("IR_SEARCH_PATH", "")
    if ir_search_path.startswith("/ABSOLUTE/PATH/TO/"):
        errors.append("IR_SEARCH_PATH still uses /ABSOLUTE/PATH/TO placeholder")
    elif ir_search_path:
        package_marker = Path(ir_search_path) / "ir_search" / "__init__.py"
        if not package_marker.exists():
            errors.append(f"IR_SEARCH_PATH is not an ir_search repository root: {ir_search_path}")

    env_file = env.get("IR_SEARCH_ENV_FILE", "")
    if env_file.startswith("/ABSOLUTE/PATH/TO/"):
        errors.append("IR_SEARCH_ENV_FILE still uses /ABSOLUTE/PATH/TO placeholder")
    elif env_file and not Path(env_file).exists():
        errors.append(f"IR_SEARCH_ENV_FILE does not exist: {env_file}")


def _validate_rules(root: Path, errors: list[str]) -> None:
    rules_dir = root / ".cursor" / "rules"
    rules_text = "\n".join(path.read_text(encoding="utf-8") for path in sorted(rules_dir.glob("*.mdc")))
    lowered_rules = rules_text.lower()
    for phrase in REQUIRED_RULE_PHRASES:
        if phrase.lower() not in lowered_rules:
            errors.append(f"Missing required rule phrase: {phrase}")

    for path in sorted(rules_dir.glob("*.mdc")):
        text = path.read_text(encoding="utf-8")
        frontmatter = _parse_mdc_frontmatter(text)
        rel = path.relative_to(root)
        if frontmatter is None:
            errors.append(f"Invalid MDC frontmatter: {rel}")
            continue
        if "description" not in frontmatter:
            errors.append(f"Missing description in MDC frontmatter: {rel}")
        if "alwaysApply" not in frontmatter:
            errors.append(f"Missing alwaysApply in MDC frontmatter: {rel}")


def _parse_mdc_frontmatter(text: str) -> dict[str, str] | None:
    lines = text.splitlines()
    if len(lines) < 4 or lines[0].strip() != "---":
        return None
    try:
        end = lines[1:].index("---") + 1
    except ValueError:
        return None
    frontmatter: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip():
            continue
        if ":" not in line:
            return None
        key, value = line.split(":", 1)
        frontmatter[key.strip()] = value.strip()
    return frontmatter


def _validate_ignore_files(root: Path, errors: list[str]) -> None:
    for rel in [".cursorignore", ".cursorindexingignore"]:
        path = root / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        lines = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
        if len(text) > 120 and text.count("\n") < 4:
            errors.append(f"{rel} appears single-line; active rules may be swallowed by comments")
        if not lines:
            errors.append(f"{rel} must contain active ignore rules")
            continue
        if lines[0] != "/*":
            errors.append(f"{rel} must use default deny mode")
        if rel == ".cursorindexingignore":
            required_rules = [
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
            ]
            for rule in required_rules:
                if rule not in lines:
                    errors.append(f"{rel} missing indexing rule: {rule}")


def _validate_line_sensitive_files(root: Path, errors: list[str]) -> None:
    candidates = [
        root / "README.md",
        root / "AGENTS.md",
        root / ".cursorignore",
        root / ".cursorindexingignore",
        root / ".cursor" / "mcp.json.template",
    ]
    candidates.extend(sorted((root / ".cursor" / "rules").glob("*.mdc")))
    candidates.extend(sorted((root / "prompts").glob("*.md")))
    for path in candidates:
        if not path.exists() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if len(text) > 120 and text.count("\n") == 0:
            errors.append(f"Line-sensitive file appears single-line: {path.relative_to(root)}")


def _validate_policy_consistency(root: Path, errors: list[str]) -> None:
    policy = _read_optional(root / ".cursor" / "rules" / "20-ir-search-evidence-policy.mdc")
    fallback = _read_optional(root / ".cursor" / "rules" / "40-fallback-policy.mdc")
    literature = _read_optional(root / "prompts" / "R-LITERATURE.md")
    readme = _read_optional(root / "README.md")
    smoke = _read_optional(root / "notes" / "smoke_test_checklist.md") + "\n" + _read_optional(root / "scripts" / "smoke_test_checklist.md")

    if "Local-only and literature exceptions" not in policy or "R-LITERATURE" not in policy:
        errors.append("Missing local-only literature exception in 20-ir-search-evidence-policy.mdc")
    if "local-only / document-first" not in literature:
        errors.append("R-LITERATURE.md must declare local-only / document-first priority")
    for status in STATUS_VALUES:
        if status not in policy:
            errors.append(f"Canonical policy missing claim status: {status}")
    if "canonical source of truth for fallback wording" not in fallback:
        errors.append("40-fallback-policy.mdc must declare canonical fallback wording")
    for text_name, text in [("README.md", readme), ("smoke checklist", smoke)]:
        if "source_health" not in text:
            errors.append(f"Missing source_health onboarding in {text_name}")


def _scan_workspace_text(root: Path, errors: list[str], warnings: list[str], *, strict: bool) -> None:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_SCAN_PARTS for part in path.relative_to(root).parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        rel = path.relative_to(root)
        for match in _find_personal_paths(text):
            message = f"Personal path found in {rel}: {match}"
            if strict:
                errors.append(message)
            else:
                warnings.append(message)
        if _contains_secret(text):
            errors.append(f"Possible secret found in {rel}")


def _find_personal_paths(text: str) -> list[str]:
    matches: list[str] = []
    for pattern in PERSONAL_PATH_PATTERNS:
        matches.extend(match.group(0) for match in pattern.finditer(text))
    return matches


def _contains_secret(text: str) -> bool:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        openai_key_prefix = "s" + "k-"
        if re.search(r"\b" + re.escape(openai_key_prefix) + r"[A-Za-z0-9_-]{12,}", stripped):
            return True
        bearer_match = re.search(r"\bBearer\s+([A-Za-z0-9._-]{8,})", stripped, flags=re.IGNORECASE)
        if bearer_match and not _is_placeholder_value(bearer_match.group(1)):
            return True
        field_match = SECRET_FIELD_RE.search(stripped)
        if field_match and not _is_placeholder_value(field_match.group(2)):
            return True
    return False


def _is_placeholder_value(value: str) -> bool:
    cleaned = value.strip().strip("\"',")
    lowered = cleaned.lower()
    if not cleaned:
        return True
    if cleaned.startswith("${env:"):
        return True
    if cleaned.startswith("/ABSOLUTE/PATH/TO/"):
        return True
    return lowered in {"your-key-here", "replace-me", "placeholder", "example", "fake-test-token", "dummy", "null", "none", "false", "true", "\"\""}


def _read_optional(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a Cursor research workspace")
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--mode", choices=["template", "generated"], default="generated")
    parser.add_argument("--strict", action="store_true", help="Treat personal paths as errors instead of warnings")
    args = parser.parse_args(argv)
    errors, warnings = collect_validation_issues(args.workspace, strict=args.strict, mode=args.mode)
    print(f"[INFO] validate_workspace version: {VALIDATOR_VERSION}")
    for warning in warnings:
        print(f"[WARN] {warning}")
    if errors:
        for error in errors:
            print(f"[ERROR] {error}")
        return 1
    print("[OK] Cursor research workspace validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
