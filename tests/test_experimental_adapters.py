import json

from ir_search.adapters.anysearch import AnySearchAdapter
from ir_search.models import Hit, Query
from ir_search.pipeline import run_pipeline, select_sources, prepare_query
from ir_search import search


class ExperimentalHitAdapter:
    name = "anysearch"
    mode = "experimental"

    def query(self, q: Query):
        return [Hit(title="t", url="https://example.com", snippet="s", source=self.name)]


def test_anysearch_adapter_mode_is_experimental():
    assert AnySearchAdapter().mode == "experimental"


def test_web_search_adapter_mode_is_experimental():
    assert AnySearchAdapter(name="web_search", auth_mode="anonymous").mode == "experimental"


def test_anysearch_hit_extra_includes_experimental_flags():
    result = run_pipeline(Query(text="test", sources=["anysearch"]), {"anysearch": ExperimentalHitAdapter()})

    assert result.diagnostics[0].adapter_mode == "experimental"
    assert result.hits[0].extra["adapter_mode"] == "experimental"


def test_anysearch_diagnostics_are_experimental_when_key_missing(monkeypatch):
    monkeypatch.delenv("ANYSEARCH_API_KEY", raising=False)

    result = search(Query(text="test", sources=["anysearch"]))

    assert result.diagnostics[0].adapter_mode == "experimental"
    assert result.diagnostics[0].ok is False


def test_anysearch_hit_extra_includes_provider_experimental_flags(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"results": [{"title": "t", "url": "https://example.com", "description": "s"}]}).encode()

    monkeypatch.setenv("ANYSEARCH_API_KEY", "test")
    monkeypatch.setattr("ir_search.adapters.anysearch.open_url", lambda *args, **kwargs: FakeResponse())

    hit = AnySearchAdapter().query(Query(text="test"))[0]

    assert hit.extra["experimental"] is True
    assert hit.extra["schema_verified"] is False


def test_experimental_adapters_are_not_in_default_source_routes():
    default_sources = set(select_sources(prepare_query(Query(text="中际旭创 最新"))))

    assert "anysearch" not in default_sources
    assert "web_search" not in default_sources
