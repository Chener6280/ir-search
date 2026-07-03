import json
import subprocess

import pytest

from ir_search.adapters.base import AdapterError
from ir_search.adapters.zsxq import ZsxqAdapter, rows_to_hits
from ir_search.kernel import build_registry
from ir_search.models import Query, SourceTier
from ir_search.pipeline import prepare_query, select_sources


def test_zsxq_adapter_uses_group_ids_and_json_search(monkeypatch):
    captured = []

    def fake_run(args, **kwargs):
        captured.append(args)
        payload = {
            "topics": [
                {
                    "topic_id": "topic-1",
                    "title": "光模块产业链观点",
                    "text": "正文摘要",
                    "created_at": "2026-06-19T09:30:00+08:00",
                    "group_name": "投研星球",
                }
            ]
        }
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=json.dumps(payload, ensure_ascii=False))

    monkeypatch.setenv("ZSXQ_CLI_COMMAND", '"/path with space/zsxq-cli"')
    monkeypatch.setenv("ZSXQ_GROUP_IDS", "123, 456")
    monkeypatch.setattr(subprocess, "run", fake_run)

    hits = ZsxqAdapter().query(Query(text="光模块"))

    assert captured == [
        ["/path with space/zsxq-cli", "topic", "+search", "--group-id", "123", "--query", "光模块", "--json"],
        ["/path with space/zsxq-cli", "topic", "+search", "--group-id", "456", "--query", "光模块", "--json"],
    ]
    assert hits[0].title == "光模块产业链观点"
    assert hits[0].source == "zsxq"
    assert hits[0].tier == SourceTier.UGC
    assert hits[0].extra["platform"] == "zsxq"
    assert hits[0].extra["group_id"] == "123"


def test_zsxq_adapter_requires_group_ids(monkeypatch):
    monkeypatch.delenv("ZSXQ_GROUP_IDS", raising=False)

    with pytest.raises(AdapterError, match="ZSXQ_GROUP_IDS"):
        ZsxqAdapter().query(Query(text="光模块"))


def test_zsxq_rows_to_hits_builds_topic_url():
    hits = rows_to_hits([
        {
            "id": "88551234",
            "content": "第一行标题\n第二行正文",
            "group_id": "123",
            "create_time": "1781842200000",
        }
    ])

    assert len(hits) == 1
    assert hits[0].title == "第一行标题"
    assert hits[0].url.endswith("/88551234")
    assert hits[0].published_at is not None


def test_zsxq_source_is_registered():
    registry = build_registry()

    assert registry["zsxq"].mode == "live"


def test_zsxq_route_is_explicitly_triggered():
    sources = select_sources(prepare_query(Query(text="知识星球 光模块 观点")))

    assert "zsxq" in sources


def test_zsxq_alias_is_supported():
    q = prepare_query(Query(text="光模块", sources=["知识星球"]))

    assert select_sources(q) == ["zsxq"]
