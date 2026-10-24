"""Deterministic lexical planning and matching: no dictionary, model or network."""
from datetime import datetime, timezone

import pytest

from ir_search import (
    AdapterMode, MaterialCandidate, MaterialCapability, MaterialKind, MaterialRegistry, MaterialSearchPage,
    MaterialSearchRequest, Provenance, search_materials,
)
from ir_search.models import EvidenceType, SourceAuthority, SourceTier
from ir_search.services.material_search import _partial_match, _strip_generic, _term_pattern

NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)


def request(question, **changes):
    args = dict(question=question, published_start="2026-09-01", published_end="2026-10-10", providers=["source_a"])
    args.update(changes)
    return MaterialSearchRequest(**args)


def candidate(index, text, title="行业观察", **changes):
    args = dict(source_ref=f"fixture://source_a/{index}", title=title, material_type="news", channel="community",
                published_on="2026-10-01", text=text, text_scope="extracted_text",
                provenance=Provenance("source_a", "fixture publisher", NOW, authority=SourceAuthority.DATA_VENDOR,
                                      source_tier=SourceTier.UGC, evidence_type=EvidenceType.OPINION,
                                      adapter_mode=AdapterMode.LIVE))
    args.update(changes)
    return MaterialCandidate(**args)


class Source:
    name = "source_a"

    def __init__(self, rows):
        self.capability = MaterialCapability(self.name, "community", tuple(MaterialKind))
        self.rows = rows

    def search_materials(self, request, *, context):
        return MaterialSearchPage(self.rows, len(self.rows), True)


def run(question, rows, **changes):
    registry = MaterialRegistry()
    registry.register(Source(rows))
    return search_materials(request(question, **changes), registry=registry)


def versions(result):
    return [version for group in result.items for version in group["versions"]]


def test_long_chinese_question_matches_text_where_the_words_are_not_adjacent():
    rows = [candidate(1, "公司表示光模块的需求出现了明显变化，订单能见度提升。"),
            candidate(2, "消费需求变化不大，终端客流平稳。")]
    result = run("光模块需求变化", rows)
    assert result.plan["topic_terms"] == ["光模块需求"] and result.plan["topic_term_basis"] == ["inferred_from_question"]
    matched = versions(result)
    assert [v["source_ref"] for v in matched] == ["fixture://source_a/1"]
    match = matched[0]["match"]
    assert match["classification"] == "partial_topic_term_match" and not match["topic_terms"]
    assert match["partial_topic_terms"] == [{"term": "光模块需求", "matched_parts": ["光模", "模块", "需求"], "coverage": "0.75"}]
    assert {gap["code"] for gap in result.gaps} >= {"partial_topic_term_matches_only"}
    # Spans still cite exact source offsets, anchored on the matched parts.
    assert matched[0]["evidence_spans"]
    for span in matched[0]["evidence_spans"]:
        assert span["text"] == matched[0][span["source_part"]][span["start_char"]:span["end_char"]]


def test_exact_topic_evidence_ranks_above_partial_and_related_evidence():
    rows = [candidate(1, "光模块的需求在三季度继续走强。"), candidate(2, "光模块需求旺盛，头部厂商满产。")]
    result = run("光模块需求变化", rows)
    ranked = [(group["versions"][0]["source_ref"], group["versions"][0]["match"]["classification"]) for group in result.items]
    assert ranked == [("fixture://source_a/2", "topic_term_match"), ("fixture://source_a/1", "partial_topic_term_match")]
    assert "partial_topic_term_matches_only" not in {gap["code"] for gap in result.gaps}


@pytest.mark.parametrize("question,expected", [
    ("宁德时代2025年三季度毛利率下滑的原因", ["三季度毛利率下滑"]),
    ("美联储降息对港股的影响", ["降息对港股"]),
    ("贵州茅台收入情况", ["收入"]),
    ("800G光模块与1.6T进展", ["800G", "光模块", "1.6T"]),
    ("NVIDIA AI capex outlook", ["NVIDIA", "AI", "capex", "outlook"]),
])
def test_generic_research_words_are_not_topic_terms(question, expected):
    result = run(question, [], dry_run=True)
    assert result.plan["topic_terms"] == expected


def test_generic_words_are_kept_when_nothing_more_specific_remains_and_keywords_are_never_rewritten():
    assert run("贵州茅台最新进展", [], dry_run=True).plan["topic_terms"] == ["最新进展"]
    explicit = run("任意问题", [], keywords=["影响", "AI"], dry_run=True).plan
    assert explicit["topic_terms"] == ["影响", "AI"] and explicit["topic_term_basis"] == ["caller_keywords"]


def test_function_words_inside_a_long_term_do_not_block_a_partial_match():
    result = run("美联储降息对港股的影响", [candidate(1, "美联储降息落地，港股科技板块领涨。", title="Federal Reserve 观察")])
    match = versions(result)[0]["match"]
    assert match["partial_topic_terms"][0]["matched_parts"] == ["降息", "港股"]


def test_ascii_terms_match_whole_tokens_only():
    rows = [candidate(1, "The CFO said capacity details will follow."),
            candidate(2, "Management raised AI capex guidance; OpenAI demand was cited."),
            candidate(3, "800G shipments doubled while 1800G samples are early.")]
    result = run("q", rows, keywords=["AI", "cap", "800G"])
    found = {v["source_ref"]: v for v in versions(result)}
    assert set(found) == {"fixture://source_a/2", "fixture://source_a/3"}
    assert found["fixture://source_a/2"]["match"]["topic_terms"] == ["AI"]
    assert found["fixture://source_a/3"]["match"]["topic_terms"] == ["800G"]
    span = found["fixture://source_a/2"]["evidence_spans"][0]
    assert span["text"][span["text"].index("AI") - 1] == " "  # anchored on the token, not inside `said`/`OpenAI`


def test_matching_primitives():
    assert _term_pattern("AI").search("said") is None and _term_pattern("ai").search("the AI cycle")
    assert _term_pattern("C++").search("uses C++ heavily") and _term_pattern("毛利率").search("单季毛利率回升")
    assert _strip_generic("最新进展") == "" and _strip_generic("光模块需求变化") == "光模块需求"
    assert _strip_generic("capex outlook") == "capex outlook"
    assert _partial_match("毛利率", "毛利和利率") is None  # short terms stay exact-only
    assert _partial_match("光模块需求", "消费需求变化不大") is None  # one shared word is not enough
