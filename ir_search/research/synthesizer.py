from __future__ import annotations

import hashlib
from urllib.parse import urlparse

from ir_search.models import EvidenceType, SourceTier
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
    official_gap_report: dict | None = None,
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

    lines.extend(
        [
            "",
            "证据表",
            "| Claim | Status | Source | Source Tier | Evidence Type | Date | Freshness | Evidence | Caveat |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for entry in claim_ledger:
        spans = dedupe_evidence_spans(entry.supporting_spans or entry.contradicting_spans, entry)
        if not spans:
            lines.append(f"| {_cell(entry.claim)} | {entry.status} | missing |  |  |  |  |  | {_cell('; '.join(entry.caveats))} |")
            continue
        for span in spans[:3]:
            date = span.published_at.date().isoformat() if span.published_at else ""
            freshness = span.extra.get("freshness_bucket", "missing_date")
            caveats = list(entry.caveats)
            if span.source_tier == SourceTier.MEDIA and span.evidence_type == EvidenceType.FINANCIAL_REPORT:
                caveats.append("warning: media source cannot be treated as financial_report")
            lines.append(
                "| "
                f"{_cell(entry.claim)} | "
                f"{entry.status} | "
                f"{_cell(span.source + ': ' + span.title[:60])} | "
                f"{span.source_tier.name} | "
                f"{span.evidence_type.value} | "
                f"{date} | "
                f"{freshness} | "
                f"{_cell(span.text[:120])} | "
                f"{_cell('; '.join(caveats))} |"
            )

    lines.extend(["", "source_matrix"])
    for row in source_matrix:
        lines.append(f"- {row['claim_id']}: final={row['final_status']}, official_filing={row['official_filing']}, media={row['media']}, wechat={row['wechat']}")

    if official_gap_report:
        lines.extend(["", "official_gap_report"])
        lines.append(f"- verdict: {official_gap_report.get('verdict', 'unknown')}")
        required = ", ".join(official_gap_report.get("official_sources_required") or []) or "none"
        with_evidence = ", ".join(official_gap_report.get("official_sources_with_evidence") or []) or "none"
        lines.append(f"- official_sources_required: {required}")
        lines.append(f"- official_sources_with_evidence: {with_evidence}")
        checklist = official_gap_report.get("manual_checklist") or []
        if checklist:
            lines.append(f"- manual_checklist: {', '.join(checklist)}")

    lines.extend(["", "未验证事项"])
    if unverified_items:
        lines.extend(f"- {item}" for item in unverified_items)
    else:
        lines.append("- none")
    return "\n".join(lines)


def dedupe_evidence_spans(spans: list[EvidenceSpan], entry: ClaimVerification) -> list[EvidenceSpan]:
    deduped: list[EvidenceSpan] = []
    seen: set[tuple[str, str, str, str]] = set()
    fallback_seen: set[tuple[str, str, str]] = set()
    for span in spans:
        key = (entry.claim_id, span.doc_id, span.url, span.span_id)
        fallback_key = (_hash(entry.claim), _canonical(span.url), _hash(span.text))
        if key in seen or fallback_key in fallback_seen:
            continue
        seen.add(key)
        fallback_seen.add(fallback_key)
        deduped.append(span)
    return deduped


def _canonical(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc.lower().removeprefix('www.')}{parsed.path}".rstrip("/")


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _cell(text: str) -> str:
    return " ".join(str(text).replace("|", "/").split())
