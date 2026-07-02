from ir_search.research import ResearchRun, deep_research


def test_deep_research_returns_auditable_run_in_mock_mode(monkeypatch):
    monkeypatch.setenv("IR_SEARCH_LIVE", "0")

    run = deep_research(
        "中际旭创 最近一季报 海外 AI 光模块需求",
        freshness="30d",
        max_searches=1,
        max_documents=3,
    )

    assert isinstance(run, ResearchRun)
    assert run.search_log
    assert run.documents_read
    assert run.evidence_spans
    assert run.claim_ledger
    assert run.source_matrix
    assert any(item["adapter_mode"] == "mock" for item in run.diagnostics)
    assert "source_text_trust: untrusted" in run.answer
