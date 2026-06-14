import json
from datetime import date

from tools import gzh_fetch


def test_infer_account_from_query(tmp_path):
    accounts = tmp_path / "accounts.json"
    accounts.write_text(
        json.dumps({"一凌策略研究": {"rss": {"url": "https://example.com/feed.xml"}}}, ensure_ascii=False),
        encoding="utf-8",
    )

    assert gzh_fetch.infer_account(str(accounts), "一凌策略研究 最新文章") == "一凌策略研究"


def test_opencli_rows_keep_crosscheck_metadata():
    result = {
        "articles": [
            {
                "title": "策略文章",
                "url": "https://mp.weixin.qq.com/s/example",
                "snippet": "摘要",
                "published_at": "2026-06-08 09:30",
                "account_name": "一凌策略研究",
                "content": "正文",
                "content_source": "wewe",
                "content_errors": [],
                "found_in": ["wewe", "rss"],
                "url_key": "sn:example",
            }
        ],
        "crosscheck": {"union": 1, "source_errors": {}},
    }

    rows = gzh_fetch.opencli_rows(result)

    assert rows == [
        {
            "title": "策略文章",
            "url": "https://mp.weixin.qq.com/s/example",
            "snippet": "摘要",
            "published_at": "2026-06-08 09:30",
            "account_name": "一凌策略研究",
            "content": "正文",
            "content_source": "wewe",
            "content_errors": [],
            "found_in": ["wewe", "rss"],
            "url_key": "sn:example",
            "crosscheck": {"union": 1, "source_errors": {}},
            "extraction_method": "gzh_crosscheck",
        }
    ]


def test_run_collects_source_errors_without_failing(tmp_path, monkeypatch):
    accounts = tmp_path / "accounts.json"
    accounts.write_text(
        json.dumps({"一凌策略研究": {"rss": {"url": "unused"}, "wewe": {"mp_id": "unused"}}}, ensure_ascii=False),
        encoding="utf-8",
    )

    def good_provider(cfg, account_name, start, end):
        return [
            gzh_fetch.Article(
                title="窗口内文章",
                url="https://mp.weixin.qq.com/s?sn=aaaaaaaaaaaaaaaaaaaaaaaa",
                published_at=gzh_fetch.parse_dt("2026-06-03 09:00"),
                account_name=account_name,
                source="rss",
            )
        ]

    def bad_provider(cfg, account_name, start, end):
        raise RuntimeError("boom")

    monkeypatch.setitem(gzh_fetch.PROVIDERS, "rss", good_provider)
    monkeypatch.setitem(gzh_fetch.PROVIDERS, "wewe", bad_provider)

    result = gzh_fetch.run(
        str(accounts),
        "一凌策略研究",
        date(2026, 6, 1),
        date(2026, 6, 7),
        ["rss", "wewe"],
        want_fulltext=False,
        emit=False,
    )

    assert result["crosscheck"]["per_source_counts"] == {"rss": 1}
    assert result["crosscheck"]["union"] == 1
    assert "wewe" in result["crosscheck"]["source_errors"]


def test_fulltext_falls_back_to_dajiala_detail(tmp_path, monkeypatch):
    accounts = tmp_path / "accounts.json"
    accounts.write_text(
        json.dumps({"一凌策略研究": {"dajiala": {"name": "一凌策略研究"}}}, ensure_ascii=False),
        encoding="utf-8",
    )

    def provider(cfg, account_name, start, end):
        return [
            gzh_fetch.Article(
                title="需要兜底正文",
                url="https://mp.weixin.qq.com/s/fallback",
                published_at=gzh_fetch.parse_dt("2026-06-03 09:00"),
                account_name=account_name,
                source="dajiala",
            )
        ]

    def broken_fulltext(url):
        raise RuntimeError("blocked")

    def detail(article):
        return "极致了详情正文"

    monkeypatch.setitem(gzh_fetch.PROVIDERS, "dajiala", provider)
    monkeypatch.setattr(gzh_fetch, "fetch_fulltext", broken_fulltext)
    monkeypatch.setattr(gzh_fetch, "fetch_dajiala_detail", detail)

    result = gzh_fetch.run(
        str(accounts),
        "一凌策略研究",
        date(2026, 6, 1),
        date(2026, 6, 7),
        ["dajiala"],
        want_fulltext=True,
        emit=False,
    )

    article = result["articles"][0]
    assert article["content"] == "极致了详情正文"
    assert article["content_source"] == "dajiala_detail"
    assert article["content_errors"][0].startswith("mp.weixin: RuntimeError: blocked")


def test_merge_uses_wechat_bmi_alias_for_sn_links():
    dajiala_article = gzh_fetch.Article(
        title="同一篇",
        url="https://mp.weixin.qq.com/s?__biz=MzA3&mid=1&idx=1",
        published_at=gzh_fetch.parse_dt("2026-06-03 09:00"),
        snippet="摘要",
        account_name="测试号",
        source="dajiala",
    )
    wewe_article = gzh_fetch.Article(
        title="同一篇",
        url="https://mp.weixin.qq.com/s?__biz=MzA3&mid=1&idx=1&sn=abcdef",
        published_at=gzh_fetch.parse_dt("2026-06-03 09:01"),
        content="正文",
        account_name="测试号",
        source="wewe",
    )

    merged, matrix = gzh_fetch.merge({"dajiala": [dajiala_article], "wewe": [wewe_article]})

    assert len(merged) == 1
    assert merged[0]["url_key"] == "wechat:sn:abcdef"
    assert merged[0]["found_in"] == ["dajiala", "wewe"]
    assert matrix[0]["dajiala"] is True
    assert matrix[0]["wewe"] is True
