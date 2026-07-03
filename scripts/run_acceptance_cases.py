from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = REPO_ROOT / "tests" / "acceptance_cases.yaml"


def load_cases(path: Path = DEFAULT_CASES) -> list[dict[str, Any]]:
    """Load the repository's restricted acceptance YAML without extra deps."""

    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
        return list(data.get("cases", []))
    except json.JSONDecodeError:
        return _parse_restricted_cases_yaml(text)


def _parse_restricted_cases_yaml(text: str) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    current_list_key: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped == "cases:":
            continue
        if line.startswith("  - "):
            if current:
                cases.append(current)
            key, value = _split_key_value(line[4:])
            current = {key: _coerce_scalar(value)}
            current_list_key = None
            continue
        if current is None:
            raise ValueError(f"Unexpected line before first case: {line}")
        if line.startswith("    ") and not line.startswith("      - "):
            key, value = _split_key_value(line[4:])
            if value == "":
                current[key] = []
                current_list_key = key
            else:
                current[key] = _coerce_scalar(value)
                current_list_key = None
            continue
        if line.startswith("      - "):
            if current_list_key is None:
                raise ValueError(f"List item without list key: {line}")
            current[current_list_key].append(_coerce_scalar(line[8:]))
            continue
        raise ValueError(f"Unsupported acceptance YAML line: {line}")
    if current:
        cases.append(current)
    return cases


def _split_key_value(text: str) -> tuple[str, str]:
    if ":" not in text:
        raise ValueError(f"Expected key/value line: {text}")
    key, value = text.split(":", 1)
    return key.strip(), value.strip()


def _coerce_scalar(value: str) -> Any:
    value = value.strip()
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def render_dry_run(cases: list[dict[str, Any]]) -> str:
    lines = ["# Acceptance Dry Run", ""]
    for case in cases:
        lines.extend(
            [
                f"## {case['id']}",
                "",
                f"- case_id: {case['id']}",
                "- cursor_self_rating: not_run",
                "- reviewer_rating: not_run",
                f"- category: {case.get('category', '')}",
                f"- requires_mcp: {str(case.get('requires_mcp', False)).lower()}",
                "- tool_calls_observed: none",
                "- run_id: missing",
                "- used_previous_run: false",
                "- evidence_basis: manual_static_check",
                "- required_tool_sequence:",
            ]
        )
        lines.extend(f"  - {tool}" for tool in case.get("required_tool_sequence", []))
        lines.append("- required_assertions:")
        lines.extend(f"  - {assertion}: not_run" for assertion in case.get("assertions", []))
        lines.append("- must_not:")
        lines.extend(f"  - {item}" for item in case.get("must_not", []))
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run or dry-run Cursor research acceptance cases")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    cases = load_cases(args.cases)
    if not cases:
        print("[ERROR] No acceptance cases found.")
        return 1
    if args.dry_run:
        print(render_dry_run(cases))
        return 0
    print("[ERROR] Live acceptance execution is intentionally manual; use --dry-run and score saved reports.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
