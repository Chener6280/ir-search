from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path


SECRET_RE = re.compile(
    r"(?i)(sk-[A-Za-z0-9_-]{12,}|Bearer\s+[A-Za-z0-9._-]{8,}|"
    r"(api[_-]?key|token|secret|cookie|authorization|session)\s*[:=]\s*[^`\s]+)"
)
RUN_ID_RE = re.compile(r"\brun_id\s*[:=]\s*(dr_[A-Za-z0-9_:-]+|[A-Za-z0-9_-]{8,})", re.I)


@dataclass
class CaseScore:
    case_id: str
    cursor_self_rating: str
    reviewer_rating: str
    tool_calls_observed: list[str]
    run_id: str
    used_previous_run: bool
    required_assertions: dict[str, str]
    evidence_basis: list[str]


def score_report(path: Path) -> list[CaseScore]:
    text = path.read_text(encoding="utf-8")
    chunks = _case_chunks(text)
    return [_score_chunk(case_id, chunk) for case_id, chunk in chunks]


def _case_chunks(text: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(r"(?m)^#{2,3}\s+([A-Za-z0-9_-]+)\s*$", text))
    if not matches:
        case_id = _field(text, "case_id") or "report"
        return [(case_id, text)]
    chunks: list[tuple[str, str]] = []
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        chunks.append((match.group(1), text[start:end]))
    return chunks


def _score_chunk(case_id: str, chunk: str) -> CaseScore:
    cursor_self_rating = _field(chunk, "cursor_self_rating") or "missing"
    reviewer_rating = _field(chunk, "reviewer_rating") or "missing"
    tool_calls = sorted(set(re.findall(r"\bir_search\.[A-Za-z_]+", chunk)))
    run_match = RUN_ID_RE.search(chunk)
    run_id = run_match.group(1) if run_match else "missing"
    assertions = {
        "no_raw_json": _pass_fail(not _looks_like_raw_json(chunk)),
        "no_secret_leak": _pass_fail(not SECRET_RE.search(chunk)),
        "reviewer_rating_present": _pass_fail(reviewer_rating not in {"", "missing", "not_run"}),
        "run_id_present": _pass_fail(run_id != "missing"),
        "source_health_not_actual_evidence": _pass_fail(not _source_health_as_evidence(chunk)),
        "placeholder_disclosed": _pass_fail("placeholder" in chunk.lower() or "mock" in chunk.lower()),
        "placeholder_not_evidence": _pass_fail(not _placeholder_as_evidence(chunk)),
        "claim_status_present": _pass_fail(any(status in chunk for status in ["supported", "mixed", "insufficient_evidence", "contradicted"])),
        "official_confirmation_requires_official_document": _pass_fail(not _unsupported_official_confirmation(chunk)),
        "freshness_caveat_present": _pass_fail(bool(re.search(r"freshness|recent_30d|recent_90d|historical|missing_date|时效|日期|新鲜度", chunk, re.I))),
        "verify_claims_called": _pass_fail("ir_search.verify_claims" in chunk),
    }
    evidence_basis = sorted(set(re.findall(r"\b(source_health|deep_research_run|claim_ledger|manual_static_check|verify_claims)\b", chunk)))
    return CaseScore(
        case_id=case_id,
        cursor_self_rating=cursor_self_rating,
        reviewer_rating=reviewer_rating,
        tool_calls_observed=tool_calls,
        run_id=run_id,
        used_previous_run=bool(re.search(r"used_previous_run\s*[:=]\s*true", chunk, re.I)),
        required_assertions=assertions,
        evidence_basis=evidence_basis or ["manual_static_check"],
    )


def _field(text: str, name: str) -> str:
    match = re.search(rf"(?im)^\s*-?\s*{re.escape(name)}\s*[:=]\s*(.+?)\s*$", text)
    return match.group(1).strip() if match else ""


def _pass_fail(value: bool) -> str:
    return "pass" if value else "fail"


def _looks_like_raw_json(text: str) -> bool:
    return bool(re.search(r"(?s)\{\s*\"(?:run_id|mcpServers|claim_ledger|evidence_spans)\"\s*:", text))


def _source_health_as_evidence(text: str) -> bool:
    return bool(
        re.search(
            r"(?i)source_health.{0,80}(is actual evidence|as actual evidence|直接证据|实质证据|officially confirmed|官方确认)",
            text,
        )
    )


def _placeholder_as_evidence(text: str) -> bool:
    for match in re.finditer(r"(?i)(placeholder|mock).{0,50}(supports|confirmed|evidence|证据|确认)", text):
        window = match.group(0).lower()
        if any(negation in window for negation in ["not ", "not_", "不能", "不可", "不是", "不作为", "不能作为"]):
            continue
        return True
    return False


def _unsupported_official_confirmation(text: str) -> bool:
    confirmation = re.search(r"(?i)(officially confirmed|官方确认|官方证实)", text)
    if not confirmation:
        return False
    official_doc = re.search(r"(?i)(official document|filing|announcement|annual report|exchange|regulator|公告|年报|季报|交易所|监管)", text)
    return official_doc is None


def render_scores(scores: list[CaseScore]) -> str:
    lines: list[str] = []
    for score in scores:
        lines.extend(
            [
                f"case_id: {score.case_id}",
                f"cursor_self_rating: {score.cursor_self_rating}",
                f"reviewer_rating: {score.reviewer_rating}",
                "tool_calls_observed:",
            ]
        )
        lines.extend(f"  - {tool}" for tool in score.tool_calls_observed)
        if not score.tool_calls_observed:
            lines.append("  - none")
        lines.extend(
            [
                f"run_id: {score.run_id}",
                f"used_previous_run: {str(score.used_previous_run).lower()}",
                "required_assertions:",
            ]
        )
        lines.extend(f"  {name}: {status}" for name, status in sorted(score.required_assertions.items()))
        lines.append("evidence_basis:")
        lines.extend(f"  - {basis}" for basis in score.evidence_basis)
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score a saved Cursor acceptance report")
    parser.add_argument("report", type=Path)
    args = parser.parse_args(argv)

    scores = score_report(args.report)
    print(render_scores(scores))
    return 0 if scores else 1


if __name__ == "__main__":
    raise SystemExit(main())
