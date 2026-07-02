from __future__ import annotations

from ir_search.evidence.models import ClaimVerification, EvidenceSpan


def synthesize_answer(
    *,
    run_id: str,
    question: str,
    search_log: list[dict],
    evidence_spans: list[EvidenceSpan],
    claim_ledger: list[ClaimVerification],
    source_matrix: list[dict],
    diagnostics: list[dict],
    unverified_items: list[str],
) -> str:
    """Render a compact finance memo from deterministic research artifacts."""

    failed = [item for item in diagnostics if not item.get("ok")]
    mock_or_placeholder = [
        item.get("source")
        for item in diagnostics
        if item.get("adapter_mode") in {"mock", "placeholder"} or item.get("skipped")
    ]
    lines = [
        "检索状态",
        f"- run_id: {run_id}",
        f"- question: {question}",
        f"- searches: {len(search_log)}",
        f"- evidence_spans: {len(evidence_spans)}",
        f"- failed_sources: {', '.join(filter(None, [item.get('source') for item in failed])) or 'none'}",
        f"- mock_or_placeholder: {', '.join(filter(None, mock_or_placeholder)) or 'none'}",
        "- source_text_trust: untrusted",
        "",
        "核心结论",
    ]
    if claim_ledger:
        for entry in claim_ledger:
            caveat = f" Caveat: {'; '.join(entry.caveats)}" if entry.caveats else ""
            lines.append(f"- [{entry.status} {entry.confidence:.2f}] {entry.claim}{caveat}")
    else:
        lines.append("- insufficient_evidence: 未抽取到可验证证据片段。")

    lines.extend(["", "证据表", "| Claim | Status | Source | Date | Evidence Type | Caveat |", "|---|---|---|---|---|---|"])
    for entry in claim_ledger:
        spans = entry.supporting_spans or entry.contradicting_spans
        if not spans:
            lines.append(f"| {entry.claim_id} | {entry.status} | missing |  |  | {'; '.join(entry.caveats)} |")
            continue
        for span in spans[:3]:
            date = span.published_at.date().isoformat() if span.published_at else ""
            caveat = "; ".join(entry.caveats)
            lines.append(
                f"| {entry.claim_id} | {entry.status} | {span.source}: {span.title[:60]} | {date} | {span.evidence_type.value} | {caveat} |"
            )

    lines.extend(["", "source_matrix"])
    for row in source_matrix:
        lines.append(f"- {row['claim_id']}: final={row['final_status']}, official_filing={row['official_filing']}, media={row['media']}, wechat={row['wechat']}")

    lines.extend(["", "未验证事项"])
    if unverified_items:
        lines.extend(f"- {item}" for item in unverified_items)
    else:
        lines.append("- none")
    return "\n".join(lines)
