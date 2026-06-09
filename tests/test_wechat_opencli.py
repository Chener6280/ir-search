import json
import subprocess

import pytest

from ir_search.adapters.base import AdapterError
from ir_search.adapters.wechat_opencli import WechatOpenCLIAdapter, parse_published_at, rows_to_hits
from ir_search.models import Query


def test_command_uses_shlex_split(monkeypatch):
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=json.dumps([{"title": "t", "url": "https://a"}]))

    monkeypatch.setenv("WECHAT_OPENCLI_COMMAND", '"/path with space/opencli" --flag "two words"')
    monkeypatch.setattr(subprocess, "run", fake_run)

    WechatOpenCLIAdapter().query(Query(text="测试"))

    assert captured["args"] == ["/path with space/opencli", "--flag", "two words", "测试"]


def test_non_json_stdout_raises_adapter_error(monkeypatch):
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="not json")

    monkeypatch.setenv("WECHAT_OPENCLI_COMMAND", "opencli")
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(AdapterError, match="not JSON"):
        WechatOpenCLIAdapter().query(Query(text="测试"))


def test_missing_required_fields_are_skipped():
    hits = rows_to_hits([
        {"title": "missing url"},
        {"url": "https://missing-title"},
        {"title": "ok", "url": "https://ok"},
    ])

    assert len(hits) == 1
    assert hits[0].title == "ok"


def test_missing_optional_fields_add_parse_warning():
    hit = rows_to_hits([{"title": "ok", "url": "https://ok"}])[0]

    assert "missing_snippet" in hit.extra["parse_warning"]
    assert "missing_account_name" in hit.extra["parse_warning"]


def test_published_at_is_parsed():
    assert parse_published_at("2026-06-08").year == 2026
    assert parse_published_at("2026/06/08").month == 6
    assert parse_published_at("2026-06-08 12:30").hour == 12
    assert parse_published_at("3小时前") is not None


def test_account_name_is_stored_in_extra():
    hit = rows_to_hits([
        {"title": "ok", "url": "https://ok", "snippet": "s", "published_at": "2026-06-08", "account_name": "账号"}
    ])[0]

    assert hit.extra["account_name"] == "账号"
    assert hit.extra["platform"] == "wechat"


def test_empty_valid_rows_raises_adapter_error(monkeypatch):
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=json.dumps([{"title": "bad"}]))

    monkeypatch.setenv("WECHAT_OPENCLI_COMMAND", "opencli")
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(AdapterError, match="no valid rows"):
        WechatOpenCLIAdapter().query(Query(text="测试"))
