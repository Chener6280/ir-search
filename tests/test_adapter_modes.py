from ir_search import Query, search
from ir_search.kernel import build_registry


def test_mock_adapter_status_is_marked():
    result = search(Query(text="中际旭创公告", sources=["cninfo"]))

    assert result.diagnostics[0].adapter_mode == "mock"


def test_mock_hits_are_marked():
    result = search(Query(text="中际旭创公告", sources=["cninfo"]))

    assert result.hits
    assert all(hit.extra["adapter_mode"] == "mock" for hit in result.hits)


def test_live_adapter_declares_mode():
    registry = build_registry(live=True)

    assert registry["bocha"].mode == "live"
    assert registry["exa"].mode == "live"
    assert registry["tavily"].mode == "live"
    assert registry["cninfo"].mode == "live"


def test_placeholder_source_not_reported_as_live():
    result = search(Query(text="中际旭创公告", sources=["sse"]))

    assert result.diagnostics[0].adapter_mode != "live"
    assert result.diagnostics[0].adapter_mode == "mock"


def test_live_official_filing_sources_are_placeholders_until_implemented():
    registry = build_registry(live=True)

    assert registry["sse"].mode == "placeholder"
    assert registry["szse"].mode == "placeholder"


def test_live_filing_placeholder_warns_not_to_rely_on_bocha():
    result = search(Query(text="中际旭创 最新公告", sources=["sse"]), registry=build_registry(live=True))

    assert result.hits == []
    assert result.diagnostics[0].adapter_mode == "placeholder"
    assert "do not rely on Bocha" in result.diagnostics[0].error
