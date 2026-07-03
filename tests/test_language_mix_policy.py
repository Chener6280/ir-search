from __future__ import annotations

from ir_search.models import Query, SearchResult
from ir_search.research.orchestrator import build_language_mix_policy, deep_research


def test_language_mix_policy_for_english_supply_chain_question():
    policy = build_language_mix_policy("Analyze overseas AI optical module demand", ["Analyze overseas AI optical module demand"])

    assert policy["query_language"] == "en"
    assert any(item["language"] == "zh" for item in policy["expanded_queries"])
    assert "Chinese sources were used" in policy["disclosure"]


def test_deep_research_outputs_language_mix_policy():
    run = deep_research(
        "Analyze overseas AI optical module demand",
        intent="industry_chain",
        max_searches=1,
        search_fn=lambda q: SearchResult(query=q, hits=[], diagnostics=[]),
        source_health_fn=lambda: {"sources": {}},
    )

    assert run.to_dict()["language_mix_policy"]["query_language"] == "en"
    assert "language_mix_policy" in run.answer
