import pytest

from ir_search.adapters.base import AdapterError
from ir_search.models import CoverageStatus, FailureKind, Hit, Intent, Query, SourceAuthority
from ir_search.pipeline import run_pipeline


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch):
    monkeypatch.setattr("ir_search.pipeline.time.sleep", lambda seconds: None)


class FailingAdapter:
    mode = "test"

    def __init__(self, name: str, error: str = "HTTP 429 rate limit") -> None:
        self.name = name
        self.error = error
        self.calls = 0

    def query(self, q: Query):
        self.calls += 1
        raise AdapterError(self.error, retryable=True)


class OneHitAdapter:
    mode = "test"

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0

    def query(self, q: Query):
        self.calls += 1
        return [
            Hit(
                title=f"{self.name} result",
                url=f"https://{self.name}.example.com/result",
                snippet="fallback hit",
                source=self.name,
            )
        ]


def test_filing_intent_blocks_search_fallbacks_even_when_policy_all():
    anysearch = OneHitAdapter("anysearch")
    searxng = OneHitAdapter("searxng")
    web_search = OneHitAdapter("web_search")

    result = run_pipeline(
        Query(
            text="600519 公告",
            intent=Intent.FILING,
            sources=["bocha"],
            allow_fallback=True,
            fallback_policy="all",
        ),
        {
            "bocha": FailingAdapter("bocha"),
            "anysearch": anysearch,
            "searxng": searxng,
            "web_search": web_search,
        },
    )

    assert anysearch.calls == 0
    assert searxng.calls == 0
    assert web_search.calls == 0
    assert result.hits == []
    assert [status.source for status in result.diagnostics] == ["bocha", "anysearch", "searxng", "web_search"]
    blocked = result.diagnostics[1:]
    assert all(status.skipped for status in blocked)
    assert all(status.failure_kind == FailureKind.BLOCKED_BY_POLICY for status in blocked)
    assert all(status.coverage_status == CoverageStatus.BLOCKED_BY_POLICY for status in blocked)
    assert all(status.fallback_parent == "bocha" for status in blocked)
    assert {status.skipped_reason for status in blocked} == {"filing_intent_requires_authoritative_source"}


def test_structured_market_data_fallback_only_uses_data_sources():
    longbridge = OneHitAdapter("longbridge")
    market_public = OneHitAdapter("market_public")
    searxng = OneHitAdapter("searxng")
    web_search = OneHitAdapter("web_search")

    result = run_pipeline(
        Query(
            text="600519 最新 PE PB 市值",
            intent=Intent.PRICE_SUPPLY_DEMAND,
            sources=["tushare"],
            allow_fallback=True,
            fallback_policy="all",
        ),
        {
            "tushare": FailingAdapter("tushare", "tushare request failed: timed out"),
            "longbridge": longbridge,
            "market_public": market_public,
            "searxng": searxng,
            "web_search": web_search,
        },
    )

    assert longbridge.calls == 1
    assert market_public.calls == 0
    assert searxng.calls == 0
    assert web_search.calls == 0
    assert result.hits[0].source == "longbridge"
    assert result.diagnostics[1].authority == SourceAuthority.BROKER_PLATFORM
    assert result.diagnostics[1].fallback_parent == "tushare"


def test_blocked_fallback_is_visible_in_diagnostics():
    result = run_pipeline(
        Query(
            text="贵州茅台 2025 年报公告",
            intent=Intent.FILING,
            sources=["bocha"],
            allow_fallback=True,
            fallback_policy="all",
        ),
        {
            "bocha": FailingAdapter("bocha"),
            "anysearch": OneHitAdapter("anysearch"),
        },
    )

    blocked = result.diagnostics[1]
    assert blocked.source == "anysearch"
    assert blocked.skipped is True
    assert blocked.adapter_mode == "skipped"
    assert blocked.failure_kind == FailureKind.BLOCKED_BY_POLICY
    assert blocked.skipped_reason == "filing_intent_requires_authoritative_source"
    assert "blocked_by_policy" in blocked.error


def test_discovery_source_requires_explicit_fallback():
    result = run_pipeline(
        Query(text="中际旭创 最新观点", sources=["bocha"], allow_fallback=False),
        {
            "bocha": FailingAdapter("bocha"),
            "anysearch": OneHitAdapter("anysearch"),
            "searxng": OneHitAdapter("searxng"),
        },
    )

    assert [status.source for status in result.diagnostics] == ["bocha"]
    assert result.hits == []
