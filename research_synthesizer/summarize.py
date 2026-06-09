from __future__ import annotations

from ir_search.models import SearchResult


def synthesize(result: SearchResult) -> dict:
    """Deterministic placeholder; an LLM implementation can replace this layer later."""
    evidence = [
        {
            "title": hit.title,
            "url": hit.url,
            "source": hit.source,
            "evidence_type": hit.evidence_type.value,
            "rank_score": hit.rank_score,
        }
        for hit in result.hits
    ]
    return {
        "core_facts": [hit["title"] for hit in evidence[:3]],
        "main_views": [],
        "bullish_views": [],
        "neutral_views": [],
        "bearish_views": [],
        "disagreements": [],
        "evidence": evidence,
        "risks": ["This deterministic placeholder does not infer unstated conclusions."],
        "to_verify": [],
        "diagnostics": [status.__dict__ for status in result.diagnostics],
    }
