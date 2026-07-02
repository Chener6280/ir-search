from ir_search.adapters.base import AdapterError
from ir_search.models import Hit, Query
from ir_search.pipeline import fallback_sources_for, run_pipeline


class FailingAdapter:
    mode = "test"

    def __init__(self, name: str, error: str) -> None:
        self.name = name
        self.error = error

    def query(self, q: Query):
        raise AdapterError(self.error, retryable=True)


class OneHitAdapter:
    mode = "test"

    def __init__(self, name: str) -> None:
        self.name = name

    def query(self, q: Query):
        extra = {}
        if self.name == "searxng":
            extra = {
                "result_kind": "discovery_url",
                "coverage_status": "partial_discovery",
                "evidence_type": "search_result",
                "confidence": "low_to_medium",
            }
        return [
            Hit(
                title=f"{self.name} fallback result",
                url=f"https://{self.name}.example.com/result",
                snippet="fallback hit",
                source=self.name,
                extra=extra,
            )
        ]


def test_commercial_sources_fallback_to_searxng_before_web_search():
    assert fallback_sources_for("bocha") == ["anysearch", "searxng", "web_search"]
    assert fallback_sources_for("exa") == ["tavily", "anysearch", "searxng", "web_search"]
    assert fallback_sources_for("searxng") == ["web_search"]


def test_searxng_is_low_priority_fallback_after_anysearch_failure():
    result = run_pipeline(
        Query(text="中际旭创 最新观点", sources=["bocha"], allow_fallback=True, fallback_policy="all"),
        {
            "bocha": FailingAdapter("bocha", "HTTP 429 rate limit"),
            "anysearch": FailingAdapter("anysearch", "HTTP 402 quota_exhausted"),
            "searxng": OneHitAdapter("searxng"),
            "web_search": OneHitAdapter("web_search"),
        },
    )

    assert [status.source for status in result.diagnostics] == ["bocha", "anysearch", "searxng"]
    assert result.hits[0].source == "searxng"
    assert result.hits[0].extra["fallback_from"] == "anysearch"
    assert result.hits[0].extra["is_fallback_result"] is True
    assert result.hits[0].extra["result_kind"] == "discovery_url"
    assert result.hits[0].extra["coverage_status"] == "partial_discovery"


def test_disabled_searxng_fallback_continues_to_web_search(monkeypatch):
    from ir_search.adapters.searxng import SearXNGAdapter

    monkeypatch.delenv("SEARXNG_ENABLED", raising=False)
    result = run_pipeline(
        Query(text="中际旭创 最新观点", sources=["anysearch"], allow_fallback=True, fallback_policy="all"),
        {
            "anysearch": FailingAdapter("anysearch", "HTTP 402 quota_exhausted"),
            "searxng": SearXNGAdapter(),
            "web_search": OneHitAdapter("web_search"),
        },
    )

    assert [status.source for status in result.diagnostics] == ["anysearch", "searxng", "web_search"]
    assert result.diagnostics[1].ok is False
    assert "SEARXNG_ENABLED" in result.diagnostics[1].error
    assert result.hits[0].source == "web_search"
