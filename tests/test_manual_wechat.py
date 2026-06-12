import json
import subprocess

from ir_search.adapters.manual_wechat import ManualWechatAdapter
from ir_search.adapters.wechat_opencli import WechatOpenCLIAdapter
from ir_search.models import Query


def test_manual_wechat_reads_markdown_front_matter(tmp_path):
    path = tmp_path / "article.md"
    path.write_text(
        """---
title: "北上重新回流"
url: "https://mp.weixin.qq.com/s/example"
published_at: "2026-06-01"
account_name: "一凌策略研究"
---
两融净流入继续放缓，国金策略观点。
""",
        encoding="utf-8",
    )

    hits = ManualWechatAdapter(root=tmp_path).query(Query(text="一凌策略研究 最新文章"))

    assert len(hits) == 1
    assert hits[0].source == "manual_wechat"
    assert hits[0].extra["account_name"] == "一凌策略研究"
    assert hits[0].published_at.year == 2026


def test_manual_wechat_reads_json_list(tmp_path):
    path = tmp_path / "articles.json"
    path.write_text(
        json.dumps(
            [
                {
                    "title": "不相关文章",
                    "url": "https://mp.weixin.qq.com/s/other",
                    "account_name": "其他账号",
                },
                {
                    "title": "牟一凌 策略观察",
                    "url": "https://mp.weixin.qq.com/s/yiling",
                    "snippet": "国金证券 策略",
                    "published_at": "2026-06-02",
                    "account_name": "一凌策略研究",
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    hits = ManualWechatAdapter(root=tmp_path).query(Query(text="牟一凌 国金证券"))

    assert [hit.title for hit in hits] == ["牟一凌 策略观察"]


def test_opencli_missing_command_uses_manual_fallback(monkeypatch, tmp_path):
    (tmp_path / "article.json").write_text(
        json.dumps(
            {
                "title": "手工文章",
                "url": "https://mp.weixin.qq.com/s/manual",
                "account_name": "一凌策略研究",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("WECHAT_OPENCLI_COMMAND", raising=False)
    monkeypatch.setenv("MANUAL_WECHAT_ROOT", str(tmp_path))

    hits = WechatOpenCLIAdapter().query(Query(text="一凌策略研究 最新文章"))

    assert hits[0].source == "manual_wechat"
    assert hits[0].extra["is_manual_wechat_fallback"] is True


def test_opencli_command_still_wins_when_configured(monkeypatch, tmp_path):
    (tmp_path / "article.json").write_text(
        json.dumps({"title": "手工文章", "url": "https://mp.weixin.qq.com/s/manual"}, ensure_ascii=False),
        encoding="utf-8",
    )

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps([{"title": "命令文章", "url": "https://mp.weixin.qq.com/s/cmd"}]),
        )

    monkeypatch.setenv("WECHAT_OPENCLI_COMMAND", "opencli")
    monkeypatch.setenv("MANUAL_WECHAT_ROOT", str(tmp_path))
    monkeypatch.setattr(subprocess, "run", fake_run)

    hits = WechatOpenCLIAdapter().query(Query(text="一凌策略研究 最新文章"))

    assert hits[0].title == "命令文章"


def test_opencli_command_failure_uses_manual_fallback(monkeypatch, tmp_path):
    (tmp_path / "article.json").write_text(
        json.dumps({"title": "手工兜底", "url": "https://mp.weixin.qq.com/s/manual", "account_name": "一凌策略研究"}, ensure_ascii=False),
        encoding="utf-8",
    )

    def fake_run(args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args, timeout=1)

    monkeypatch.setenv("WECHAT_OPENCLI_COMMAND", "opencli")
    monkeypatch.setenv("MANUAL_WECHAT_ROOT", str(tmp_path))
    monkeypatch.setattr(subprocess, "run", fake_run)

    hits = WechatOpenCLIAdapter().query(Query(text="一凌策略研究 最新文章"))

    assert hits[0].title == "手工兜底"
    assert "timed out" in hits[0].extra["wechat_opencli_error"]
