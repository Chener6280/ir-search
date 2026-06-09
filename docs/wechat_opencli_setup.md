# WeChat OpenCLI Setup

`wechat_opencli` is a controlled browser automation adapter for WeChat public-account articles. It is not exposed as a standalone MCP tool.

## Configure Command

Set `WECHAT_OPENCLI_COMMAND` to an external command that accepts the query as the last argument and prints JSON to stdout.

```bash
export WECHAT_OPENCLI_COMMAND="/path/to/opencli-wechat-search --json"
```

Optional timeout:

```bash
export WECHAT_OPENCLI_TIMEOUT=60
```

## Expected stdout schema

The command must print a JSON list:

```json
[
  {
    "title": "文章标题",
    "url": "https://mp.weixin.qq.com/...",
    "snippet": "摘要或正文前几百字",
    "published_at": "2026-06-08",
    "account_name": "公众号名称"
  }
]
```

Required fields:

```text
title
url
```

Optional fields:

```text
snippet
published_at
account_name
```

Rows missing required fields are skipped. Rows missing optional fields are kept with `Hit.extra["parse_warning"]`.

## Common errors

```text
WECHAT_OPENCLI_COMMAND is not set
wechat opencli command not found
wechat opencli timed out
wechat opencli stdout is not JSON
wechat opencli stdout must be a JSON list
wechat opencli returned no valid rows
```

## Live smoke test

Live tests are skipped by default:

```bash
IR_SEARCH_RUN_LIVE_TESTS=1 pytest tests/test_wechat_opencli_live.py
```

The smoke test expects a working command, valid login state, and JSON stdout.
