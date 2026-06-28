import json
import subprocess

import pytest

from ir_search.adapters.base import AdapterError
from ir_search.adapters.longbridge import (
    LongbridgeAdapter,
    ensure_read_only_args,
    symbols_from_query,
)
from ir_search.kernel import build_registry
from ir_search.models import EvidenceType, Query
from ir_search.pipeline import prepare_query, select_sources


def test_longbridge_adapter_uses_read_only_news_quote_and_rating(monkeypatch):
    captured = []

    def fake_run(args, **kwargs):
        captured.append(args)
        if args[1:3] == ["news", "search"]:
            payload = {
                "items": [
                    {
                        "id": "282276051",
                        "title": "NVDA AI server demand update",
                        "summary": "Blackwell demand remains strong",
                        "published_at": "2026-06-20T02:16:27Z",
                        "url": "https://longbridge.com/news/282276051",
                    }
                ]
            }
        elif args[1] == "quote":
            payload = [{"symbol": args[2], "last_done": "183.91", "change_rate": "1.2%", "volume": "100"}]
        elif args[1] == "institution-rating":
            payload = {"analyst": {"evaluate": {"buy": 18, "hold": 5, "sell": 1}, "target": {"highest_price": "250"}}}
        else:
            raise AssertionError(f"unexpected command: {args}")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=json.dumps(payload))

    monkeypatch.setenv("LONGBRIDGE_CLI_COMMAND", "/usr/local/bin/longbridge")
    monkeypatch.setattr(subprocess, "run", fake_run)

    hits = LongbridgeAdapter().query(Query(text="NVDA.US 最新 AI 新闻", count=5))

    assert captured == [
        ["/usr/local/bin/longbridge", "news", "search", "NVDA.US 最新 AI 新闻", "--count", "10", "--format", "json"],
        ["/usr/local/bin/longbridge", "quote", "NVDA.US", "--format", "json"],
        ["/usr/local/bin/longbridge", "institution-rating", "NVDA.US", "--format", "json"],
    ]
    assert [hit.extra["kind"] for hit in hits] == ["news", "quote", "institution_rating"]
    assert all(hit.extra["read_only"] for hit in hits)
    assert hits[0].evidence_type == EvidenceType.NEWS
    assert hits[1].evidence_type == EvidenceType.DATA_TABLE


def test_longbridge_read_only_guard_blocks_portfolio_and_trading():
    with pytest.raises(AdapterError, match="read-only"):
        ensure_read_only_args(["portfolio", "--format", "json"])

    with pytest.raises(AdapterError, match="blocked"):
        ensure_read_only_args(["quote", "NVDA.US", "order", "--format", "json"])


def test_longbridge_symbol_detection_supports_explicit_and_a_share_codes():
    assert symbols_from_query("NVDA.US 700.HK 600519 300750") == ["NVDA.US", "700.HK", "600519.SH", "300750.SZ"]


def test_longbridge_source_is_registered():
    registry = build_registry()

    assert registry["longbridge"].mode == "live"


def test_longbridge_route_is_explicitly_triggered():
    sources = select_sources(prepare_query(Query(text="长桥 NVDA.US 最新新闻")))

    assert "longbridge" in sources


def test_longbridge_alias_is_supported():
    q = prepare_query(Query(text="NVDA.US", sources=["长桥"]))

    assert select_sources(q) == ["longbridge"]
