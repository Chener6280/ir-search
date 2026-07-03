import pytest

from ir_search.adapters.base import AdapterError
from ir_search.models import Hit, Query
from ir_search.pipeline import classify_fallback_error, fallback_sources_for, retry_backoff_seconds, run_pipeline


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch):
    monkeypatch.setattr("ir_search.pipeline.time.sleep", lambda seconds: None)


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
        return [
            Hit(
                title=f"{self.name} fallback result",
                url=f"https://{self.name}.example.com/result",
                snippet="fallback hit",
                source=self.name,
            )
        ]


class EmptyAdapter:
    mode = "test"

    def __init__(self, name: str) -> None:
        self.name = name

    def query(self, q: Query):
        return []


class FlakyAdapter:
    mode = "test"

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0

    def query(self, q: Query):
        self.calls += 1
        if self.calls == 1:
            raise AdapterError("temporary timeout", retryable=True)
        return [
            Hit(
                title="recovered",
                url="https://recovered.example.com",
                snippet="retry success",
                source=self.name,
            )
        ]


def test_no_fallback_by_default():
    result = run_pipeline(
        Query(text="中际旭创 最新", sources=["bocha"]),
        {
            "bocha": FailingAdapter("bocha", "HTTP 402 quota_exhausted"),
            "anysearch": OneHitAdapter("anysearch"),
            "web_search": OneHitAdapter("web_search"),
        },
    )

    assert [status.source for status in result.diagnostics] == ["bocha"]
    assert result.hits == []


def test_allow_fallback_explicitly():
    result = run_pipeline(
        Query(text="中际旭创 最新", sources=["bocha"], allow_fallback=True, fallback_policy="all"),
        {
            "bocha": FailingAdapter("bocha", "HTTP 402 quota_exhausted"),
            "anysearch": OneHitAdapter("anysearch"),
            "web_search": OneHitAdapter("web_search"),
        },
    )

    assert [status.source for status in result.diagnostics] == ["bocha", "anysearch"]
    assert result.failed_sources == ["bocha"]
    assert result.hits[0].source == "anysearch"
    assert "fallback_after=bocha" in result.diagnostics[1].error
    assert result.hits[0].extra["fallback_from"] == "bocha"
    assert result.hits[0].extra["is_fallback_result"] is True


def test_retryable_error_is_retried_once_before_fallback():
    flaky = FlakyAdapter("bocha")
    result = run_pipeline(
        Query(text="中际旭创 最新", sources=["bocha"], allow_fallback=True, fallback_policy="all"),
        {
            "bocha": flaky,
            "anysearch": OneHitAdapter("anysearch"),
        },
    )

    assert flaky.calls == 2
    assert [status.source for status in result.diagnostics] == ["bocha"]
    assert result.hits[0].source == "bocha"
    assert "retry_after=temporary timeout" in result.diagnostics[0].error


def test_retryable_quota_error_backs_off_before_retry(monkeypatch):
    sleeps = []
    monkeypatch.setattr("ir_search.pipeline.time.sleep", sleeps.append)

    run_pipeline(
        Query(text="中际旭创 最新", sources=["bocha"], allow_fallback=True, fallback_policy="all"),
        {
            "bocha": FailingAdapter("bocha", "HTTP 429 rate limit"),
            "anysearch": OneHitAdapter("anysearch"),
        },
    )

    assert sleeps == [retry_backoff_seconds("HTTP 429 rate limit")]


def test_fallback_on_empty_is_explicit():
    result = run_pipeline(
        Query(text="中际旭创 最新", sources=["bocha"], allow_fallback=True, fallback_policy="all"),
        {
            "bocha": EmptyAdapter("bocha"),
            "anysearch": OneHitAdapter("anysearch"),
        },
    )

    assert [status.source for status in result.diagnostics] == ["bocha"]
    assert result.hits == []


def test_fallback_on_empty_uses_configured_route():
    result = run_pipeline(
        Query(
            text="中际旭创 最新",
            sources=["bocha"],
            allow_fallback=True,
            fallback_policy="all",
            fallback_on_empty=True,
        ),
        {
            "bocha": EmptyAdapter("bocha"),
            "anysearch": OneHitAdapter("anysearch"),
        },
    )

    assert [status.source for status in result.diagnostics] == ["bocha", "anysearch"]
    assert result.hits[0].source == "anysearch"
    assert "fallback_after=bocha" in result.diagnostics[1].error


def test_fallback_diagnostics_are_recorded():
    result = run_pipeline(
        Query(text="NVDA sentiment", sources=["exa"], allow_fallback=True, fallback_policy="all"),
        {
            "exa": FailingAdapter("exa", "HTTP 429 rate limit"),
            "tavily": FailingAdapter("tavily", "TAVILY_API_KEY is not set"),
            "anysearch": OneHitAdapter("anysearch"),
        },
    )

    assert [status.source for status in result.diagnostics] == [
        "exa",
        "tavily",
        "anysearch",
    ]
    assert result.failed_sources == ["exa", "tavily"]
    assert result.hits[0].source == "anysearch"
    assert "fallback_after=tavily" in result.diagnostics[2].error


def test_fallback_hits_are_marked():
    result = run_pipeline(
        Query(text="中际旭创 最新", sources=["bocha"], allow_fallback=True, fallback_policy="all"),
        {
            "bocha": FailingAdapter("bocha", "HTTP 429 rate limit"),
            "anysearch": FailingAdapter("anysearch", "HTTP 402 quota_exhausted"),
            "web_search": OneHitAdapter("web_search"),
        },
    )

    assert [status.source for status in result.diagnostics] == ["bocha", "anysearch", "searxng", "web_search"]
    assert result.hits[0].source == "web_search"
    assert result.hits[0].extra["fallback_from"] == "searxng"
    assert result.hits[0].extra["is_fallback_result"] is True


def test_web_fallback_routes_do_not_include_zsxq():
    assert fallback_sources_for("bocha") == ["anysearch", "searxng", "web_search"]
    assert fallback_sources_for("exa") == ["tavily", "anysearch", "searxng", "web_search"]
    assert fallback_sources_for("web_search") == []


def test_non_quota_unknown_source_does_not_fallback():
    result = run_pipeline(Query(text="中际旭创", sources=["missing"]), {})

    assert [status.source for status in result.diagnostics] == ["missing"]
    assert result.hits == []


def test_error_classification_uses_fallback_config():
    assert classify_fallback_error("HTTP 429 rate limit") == "quota"
    assert classify_fallback_error("urlopen connection timed out") == "network"
    assert classify_fallback_error("semantic parse failed") == "unknown"
