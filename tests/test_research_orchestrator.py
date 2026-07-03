from ir_search.documents.models import Document, make_doc_id, utc_now
from ir_search.models import EvidenceType, Hit, Query, SearchResult, SourceStatus, SourceTier
from ir_search.research.orchestrator import draft_claim_candidates, deep_research


def test_draft_claims_are_question_or_template_first():
    candidates = draft_claim_candidates("中际旭创 最新季报是否验证需求", "earnings", [])

    assert candidates[0]["origin"] == "question"
    assert candidates[1]["origin"] == "template"


def test_evidence_origin_claims_are_not_primary_conclusions():
    candidates = draft_claim_candidates(
        "中际旭创 最新季报是否验证需求",
        "earnings",
        [_span("公司披露收入增长。")],
    )

    assert candidates[0]["origin"] != "evidence"
    assert any(candidate["origin"] == "evidence" for candidate in candidates)


def test_earnings_intent_generates_financial_claim_templates():
    candidates = draft_claim_candidates("业绩是否验证需求", "earnings", [])

    assert any("收入" in candidate["claim"] or "毛利率" in candidate["claim"] for candidate in candidates)


def test_policy_intent_generates_policy_claim_templates():
    candidates = draft_claim_candidates("政策有什么变化", "policy", [])

    assert any("政策文本" in candidate["claim"] for candidate in candidates)


def test_wechat_crosscheck_intent_treats_wechat_as_candidate():
    candidates = draft_claim_candidates("公众号说涨价", "wechat_crosscheck", [])

    assert any("微信文章只作为候选来源" in candidate["claim"] for candidate in candidates)


def test_orchestrator_runs_source_health_before_search():
    calls = []

    def source_health_fn():
        calls.append("health")
        return {"sources": {"cninfo": {"ok": False, "adapter_mode": "placeholder", "notes": ["not implemented"]}}}

    def search_fn(q):
        calls.append("search")
        return SearchResult(query=q, hits=[], diagnostics=[])

    deep_research("中际旭创 最新季报", intent="earnings", max_searches=1, search_fn=search_fn, source_health_fn=source_health_fn)

    assert calls[:2] == ["health", "search"]


def test_source_health_placeholder_appears_in_diagnostics():
    run = deep_research(
        "中际旭创 最新季报",
        intent="earnings",
        max_searches=1,
        search_fn=lambda q: SearchResult(query=q, hits=[], diagnostics=[]),
        source_health_fn=lambda: {"sources": {"cninfo": {"ok": False, "adapter_mode": "placeholder", "notes": ["not implemented"]}}},
    )

    assert run.diagnostics[0]["source_health"] is True
    assert run.diagnostics[0]["adapter_mode"] == "placeholder"


def test_required_source_placeholder_visible_in_answer():
    run = deep_research(
        "中际旭创 最新季报",
        intent="earnings",
        max_searches=1,
        search_fn=lambda q: SearchResult(query=q, hits=[], diagnostics=[]),
        source_health_fn=lambda: {"sources": {"cninfo": {"ok": False, "adapter_mode": "placeholder", "notes": ["not implemented"]}}},
    )

    assert "部分权威源当前为 mock/placeholder" in run.answer


def test_max_documents_budget_respected():
    def search_fn(q):
        hits = [
            Hit(title=f"title {idx}", url=f"https://example.com/{idx}", snippet="公司收入增长。", source="cninfo", tier=SourceTier.EXCHANGE_FILING)
            for idx in range(5)
        ]
        return SearchResult(query=q, hits=hits, diagnostics=[])

    run = deep_research("中际旭创 最新季报", intent="earnings", max_searches=1, max_documents=2, search_fn=search_fn)

    assert len(run.documents_read) == 2


def _span(text):
    return type(
        "Span",
        (),
        {"text": text},
    )()
