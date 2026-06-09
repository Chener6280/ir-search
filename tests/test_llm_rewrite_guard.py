from ir_search import Query, search


def test_llm_rewrite_default_false():
    assert Query(text="中际旭创").llm_rewrite is False


def test_search_does_not_call_llm_rewrite_by_default(monkeypatch):
    called = {"value": False}
    original_import = __import__

    def fake_import(name, *args, **kwargs):
        if "llm" in name.lower() or "openai" in name.lower():
            called["value"] = True
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    result = search(Query(text="中际旭创", sources=["cninfo"]))

    assert len(result.hits) > 0
    assert called["value"] is False


def test_llm_rewrite_flag_is_reserved():
    result = search(Query(text="中际旭创", sources=["cninfo"], llm_rewrite=True))

    assert len(result.hits) > 0
    assert result.query.llm_rewrite is True
