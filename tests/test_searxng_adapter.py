import json

import pytest

from ir_search.adapters.base import AdapterError
from ir_search.adapters.searxng import SearXNGAdapter, build_searxng_url
from ir_search.models import EvidenceType, Lang, Query


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


def test_searxng_disabled_by_default(monkeypatch):
    monkeypatch.delenv("SEARXNG_ENABLED", raising=False)
    monkeypatch.delenv("SEARXNG_URL", raising=False)

    with pytest.raises(AdapterError, match="SEARXNG_ENABLED"):
        SearXNGAdapter().query(Query(text="中际旭创 光模块"))


def test_build_searxng_url_uses_json_api_and_engines():
    url = build_searxng_url(
        "http://localhost:8080",
        Query(text="中际旭创 光模块", lang=Lang.ZH),
        ["bing", "duckduckgo"],
    )

    assert url.startswith("http://localhost:8080/search?")
    assert "format=json" in url
    assert "engines=bing%2Cduckduckgo" in url
    assert "language=zh-CN" in url


def test_searxng_parses_json_results(monkeypatch):
    captured = {}

    def fake_open_url(req, timeout, **kwargs):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        captured["kwargs"] = kwargs
        return FakeResponse(
            {
                "results": [
                    {
                        "title": "中信证券 AI 算力观点",
                        "url": "https://example.com/research",
                        "content": "公开网页摘要",
                        "engine": "bing",
                        "score": 0.72,
                    }
                ]
            }
        )

    monkeypatch.setenv("SEARXNG_ENABLED", "true")
    monkeypatch.setenv("SEARXNG_URL", "http://localhost:8080")
    monkeypatch.setenv("SEARXNG_TIMEOUT", "7")
    monkeypatch.setattr("ir_search.adapters.searxng.open_url", fake_open_url)

    hit = SearXNGAdapter().query(Query(text="中信证券 AI 算力", count=5))[0]

    assert "format=json" in captured["url"]
    assert captured["timeout"] == 7
    assert captured["kwargs"]["disable_proxy"] is True
    assert hit.source == "searxng"
    assert hit.title == "中信证券 AI 算力观点"
    assert hit.evidence_type == EvidenceType.UNKNOWN
    assert hit.fetched_at is not None
    assert hit.extra["adapter_mode"] == "fallback"
    assert hit.extra["result_kind"] == "discovery_url"
    assert hit.extra["coverage_status"] == "partial_discovery"
    assert hit.extra["evidence_type"] == "search_result"
    assert hit.extra["confidence"] == "low_to_medium"
    assert hit.extra["promotable"] is True
    assert hit.extra["promotion_required"] is True
    assert hit.extra["query"] == "中信证券 AI 算力"
    assert hit.extra["engine"] == "bing"
    assert hit.extra["rank"] == 1


def test_searxng_failure_is_logged(monkeypatch, tmp_path):
    log_path = tmp_path / "searxng_failures.log"

    def fail_open_url(*args, **kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setenv("SEARXNG_ENABLED", "true")
    monkeypatch.setenv("SEARXNG_FAILURE_LOG", str(log_path))
    monkeypatch.setattr("ir_search.adapters.searxng.open_url", fail_open_url)

    with pytest.raises(AdapterError, match="timed out"):
        SearXNGAdapter().query(Query(text="光模块 研报"))

    row = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert row["query"] == "光模块 研报"
    assert row["error_type"] == "timeout"
    assert "timed out" in row["error_message"]
