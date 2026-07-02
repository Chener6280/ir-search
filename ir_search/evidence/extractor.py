from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

from ir_search.documents.models import Document
from ir_search.models import EvidenceType, SourceTier

from .models import EvidenceSpan


@dataclass
class TextChunk:
    text: str
    start: int
    end: int
    section: Optional[str] = None
    page: Optional[int] = None


def extract_evidence(
    document: Document,
    question: str,
    *,
    max_spans: int = 20,
    max_span_chars: int = 1200,
) -> list[EvidenceSpan]:
    """Return deterministic evidence spans relevant to a question."""

    if max_spans <= 0 or not document.text.strip() or not question.strip():
        return []

    question_terms = terms_for_text(question)
    if not question_terms:
        return []

    spans: list[EvidenceSpan] = []
    for chunk in chunk_document_text(document.text, max_span_chars=max_span_chars):
        score = relevance_score(
            question_terms,
            terms_for_text(chunk.text),
            source_tier=document.source_tier,
            evidence_type=document.evidence_type,
            published_at=document.published_at,
        )
        if score <= 0:
            continue
        text = chunk.text.strip()
        if len(text) > max_span_chars:
            text = text[:max_span_chars].rstrip()
        spans.append(
            EvidenceSpan(
                span_id=make_span_id(document.doc_id, question, chunk.start, chunk.end, text),
                doc_id=document.doc_id,
                url=document.url,
                title=document.title,
                source=document.source,
                source_tier=document.source_tier,
                evidence_type=document.evidence_type,
                text=text,
                relevance_score=round(score, 4),
                page=chunk.page,
                section=chunk.section,
                start_char=chunk.start,
                end_char=chunk.end,
                published_at=document.published_at,
                extracted_for_question=question,
                extra={
                    "source_text_trust": "untrusted",
                    "adapter_mode": document.extra.get("adapter_mode"),
                    "content_type": document.content_type,
                    "document_warnings": list(document.warnings),
                },
            )
        )

    spans.sort(key=lambda span: (span.relevance_score, span.source_tier.value, len(span.text)), reverse=True)
    return spans[:max_spans]


def chunk_document_text(text: str, *, max_span_chars: int = 1200) -> list[TextChunk]:
    """Split source text into paragraph-like chunks while preserving offsets."""

    chunks: list[TextChunk] = []
    current_section: Optional[str] = None
    current_page: Optional[int] = None
    for match in re.finditer(r"\S(?:.*?)(?=\n\s*\n|\Z)", text, flags=re.S):
        raw = match.group(0).strip()
        if not raw:
            continue
        page_match = re.match(r"(?:page|第)\s*(\d+)\s*(?:页)?", raw, flags=re.I)
        if page_match:
            current_page = int(page_match.group(1))
        first_line = raw.splitlines()[0].strip()
        if len(first_line) <= 80 and (first_line.endswith(":") or first_line.startswith("#")):
            current_section = first_line.strip("#: ")
        for part, start_delta in _split_long_chunk(raw, max_span_chars=max_span_chars):
            start = match.start() + start_delta
            chunks.append(
                TextChunk(
                    text=part,
                    start=start,
                    end=start + len(part),
                    section=current_section,
                    page=current_page,
                )
            )
    return chunks


def relevance_score(
    question_terms: set[str],
    chunk_terms: set[str],
    *,
    source_tier: SourceTier,
    evidence_type: EvidenceType,
    published_at: Optional[datetime],
) -> float:
    if not question_terms or not chunk_terms:
        return 0.0
    overlap = question_terms & chunk_terms
    if not overlap:
        return 0.0
    base = len(overlap) / max(3, len(question_terms))
    tier_bonus = min(0.16, source_tier.value * 0.025)
    evidence_bonus = {
        EvidenceType.FINANCIAL_REPORT: 0.1,
        EvidenceType.ANNOUNCEMENT: 0.09,
        EvidenceType.POLICY_DOC: 0.08,
        EvidenceType.EARNINGS_CALL: 0.06,
        EvidenceType.BROKER_REPORT: 0.04,
        EvidenceType.DATA_TABLE: 0.04,
    }.get(evidence_type, 0.0)
    freshness = freshness_bonus(published_at)
    return min(1.0, base + tier_bonus + evidence_bonus + freshness)


def freshness_bonus(published_at: Optional[datetime]) -> float:
    if not published_at:
        return 0.0
    dt = published_at
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    days = max(0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).days)
    if days <= 30:
        return 0.04
    if days <= 90:
        return 0.02
    return 0.0


def terms_for_text(text: str) -> set[str]:
    """Tokenize mixed Chinese/English finance text without external dependencies."""

    lower = text.lower()
    terms = {token for token in re.findall(r"[a-z0-9][a-z0-9._+-]{1,}", lower) if token not in STOP_WORDS}
    chinese_runs = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    for run in chinese_runs:
        for n in (2, 3, 4):
            if len(run) >= n:
                terms.update(run[idx : idx + n] for idx in range(0, len(run) - n + 1))
        if len(run) <= 8:
            terms.add(run)
    stock_codes = re.findall(r"\b\d{6}\b", text)
    terms.update(stock_codes)
    return terms


def make_span_id(doc_id: str, question: str, start: int, end: int, text: str) -> str:
    digest = hashlib.sha1(f"{doc_id}|{question}|{start}|{end}|{text}".encode("utf-8")).hexdigest()[:16]
    return f"sp_{digest}"


def _split_long_chunk(text: str, *, max_span_chars: int) -> Iterable[tuple[str, int]]:
    if len(text) <= max_span_chars:
        yield text, 0
        return
    offset = 0
    while offset < len(text):
        end = min(len(text), offset + max_span_chars)
        if end < len(text):
            sentence_end = max(text.rfind("。", offset, end), text.rfind(".", offset, end), text.rfind("\n", offset, end))
            if sentence_end > offset + max_span_chars // 2:
                end = sentence_end + 1
        yield text[offset:end].strip(), offset
        offset = end


STOP_WORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
    "是否",
    "最近",
    "最新",
    "分析",
    "关于",
    "一个",
}
