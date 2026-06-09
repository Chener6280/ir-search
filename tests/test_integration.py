from ir_search import Query, SearchResult, search


def test_search_returns_result_with_diagnostics():
    result = search("中际旭创 一季报")

    assert isinstance(result, SearchResult)
    assert len(result.hits) > 0
    assert result.query.intent.value == "earnings"
    assert all(status.source for status in result.diagnostics)


def test_unknown_forced_source_is_diagnostic_failure():
    result = search(Query(text="中际旭创", sources=["not_real"]))

    assert result.hits == []
    assert result.failed_sources == ["not_real"]
    assert result.diagnostics[0].error == "unknown source"


def test_wechat_failure_is_explicit_when_triggered():
    result = search(Query(text="某公众号 最新文章", sources=["wechat"]))

    assert result.failed_sources == ["wechat_opencli"]
    assert "WECHAT_OPENCLI_COMMAND" in result.diagnostics[0].error
