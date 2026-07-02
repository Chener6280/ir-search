import json
from datetime import date

import pytest

from ir_search.adapters.base import AdapterError
from ir_search.adapters.dajiala import DajialaAdapter
from ir_search.kernel import build_registry
from ir_search.models import Query, TimeWindow
from ir_search.pipeline import prepare_query, select_sources


def test_dajiala_adapter_uses_only_dajiala_provider(tmp_path, monkeypatch):
    accounts = tmp_path / "accounts.json"
    accounts.write_text(json.dumps({"一凌策略研究": {"dajiala": {"name": "一凌策略研究"}}}, ensure_ascii=False), encoding="utf-8")
    captured = {}

    def fake_run(accounts_path, account, start, end, providers, want_fulltext, emit):
        captured.update(
            {
                "accounts_path": accounts_path,
                "account": account,
                "start": start,
                "end": end,
                "providers": providers,
                "want_fulltext": want_fulltext,
                "emit": emit,
            }
        )
        return {
            "articles": [
                {
                    "title": "策略文章",
                    "url": "https://mp.weixin.qq.com/s/example",
                    "snippet": "摘要",
                    "published_at": "2026-06-08 09:30",
                    "account_name": "一凌策略研究",
                    "content": "",
                    "content_source": "",
                    "content_errors": [],
                    "found_in": ["dajiala"],
                    "url_key": "wechat:tok:example",
                }
            ],
            "crosscheck": {"per_source_counts": {"dajiala": 1}, "union": 1, "source_errors": {}},
        }

    monkeypatch.setenv("DAJIALA_KEY", "key")
    monkeypatch.setenv("DAJIALA_ACCOUNTS_PATH", str(accounts))
    monkeypatch.setenv("DAJIALA_FULLTEXT", "1")
    monkeypatch.setattr("tools.gzh_fetch.run", fake_run)

    hits = DajialaAdapter().query(Query(text="一凌策略研究 最新文章", window=TimeWindow(raw="oneWeek")))

    assert captured["providers"] == ["dajiala"]
    assert captured["want_fulltext"] is True
    assert captured["emit"] is False
    assert captured["start"] <= captured["end"]
    assert hits[0].source == "dajiala"
    assert hits[0].extra["provider"] == "dajiala"
    assert hits[0].extra["provider_only"] is True
    assert hits[0].extra["requires_login"] is False


def test_dajiala_requires_key(monkeypatch):
    monkeypatch.delenv("DAJIALA_KEY", raising=False)

    with pytest.raises(AdapterError, match="DAJIALA_KEY"):
        DajialaAdapter().query(Query(text="一凌策略研究 最新文章"))


def test_dajiala_requires_accounts_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DAJIALA_KEY", "key")
    monkeypatch.setenv("DAJIALA_ACCOUNTS_PATH", str(tmp_path / "missing.json"))

    with pytest.raises(AdapterError, match="DAJIALA_ACCOUNTS_PATH"):
        DajialaAdapter().query(Query(text="一凌策略研究 最新文章"))


def test_dajiala_source_is_registered():
    registry = build_registry()

    assert registry["dajiala"].mode == "live"


def test_dajiala_route_is_explicitly_triggered():
    sources = select_sources(prepare_query(Query(text="极致了 一凌策略研究 最新文章")))

    assert "dajiala" in sources


def test_dajiala_alias_is_supported():
    q = prepare_query(Query(text="一凌策略研究", sources=["极致了"]))

    assert select_sources(q) == ["dajiala"]
