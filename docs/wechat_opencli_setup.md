# WeChat OpenCLI Setup

`wechat_opencli` is a controlled browser automation adapter for WeChat public-account articles. It is not exposed as a standalone MCP tool.

If `WECHAT_OPENCLI_COMMAND` is not configured, the adapter tries `manual_wechat` as a deterministic local fallback. This lets the project keep important WeChat articles searchable even when WeChat automation is unavailable.

## Configure Command

Set `WECHAT_OPENCLI_COMMAND` to an external command that accepts the query as the last argument and prints JSON to stdout.

```bash
export WECHAT_OPENCLI_COMMAND="/path/to/opencli-wechat-search --json"
```

Optional timeout:

```bash
export WECHAT_OPENCLI_TIMEOUT=60
```

Public-search candidate command:

```bash
export WECHAT_OPENCLI_COMMAND="python3 /Users/chen/Documents/Codex/2026-06-08/files-mentioned-by-the-user-ir/tools/wechat_search_sogou.py --json"
```

This command searches public Sogou Weixin result pages and prints candidate articles in the expected JSON shape. It can be blocked by anti-spider verification and should not be treated as a fully reliable latest-article source.

## GZH Cross-Check Command

For automated official-account retrieval, prefer `tools/gzh_fetch.py`. It implements three independent providers:

```text
dajiala -> 极致了 API，requires DAJIALA_KEY
wewe    -> self-hosted wewe-rss / we-mp-rss JSON feed
rss     -> generic RSS/Atom feed, including RSSHub routes
```

Important credential boundary:

```text
wewe-rss uses a WeChat Reading account only inside its own management UI.
This project never receives the WeChat Reading account/password.
ir_search only reads local feed output such as http://127.0.0.1:4000/feeds/xxx.json.
```

Create a private account mapping file from [configs/accounts.example.json](../configs/accounts.example.json):

```bash
cp configs/accounts.example.json accounts.json
```

Example command:

```bash
export DAJIALA_KEY="..."
export WECHAT_OPENCLI_COMMAND="python3 /Users/chen/Documents/Codex/2026-06-08/files-mentioned-by-the-user-ir/tools/gzh_fetch.py --accounts /Users/chen/Documents/Codex/2026-06-08/files-mentioned-by-the-user-ir/accounts.json --opencli --providers dajiala,wewe,rss --default-days 14"
```

Then run:

```bash
python3 -m ir_search "一凌策略研究 最新文章" --source wechat --count 5
```

`--opencli` prints a JSON list compatible with `WECHAT_OPENCLI_COMMAND`. Without `--opencli`, the tool prints the full object:

```json
{
  "articles": [],
  "crosscheck": {
    "per_source_counts": {},
    "union": 0,
    "matrix": [],
    "only_in_one_source": [],
    "source_errors": {}
  }
}
```

`wechat_opencli` can also parse this full object and preserves `crosscheck`, `found_in`, `url_key`, `content`, `content_source`, and `content_errors` in `Hit.extra`.

Limitations:

```text
DAJIALA_ENDPOINT, DAJIALA_DETAIL_ENDPOINT, and the response field candidates are marked VERIFY in tools/gzh_fetch.py.
Check them against the 极致了 console docs; if they differ, update the constants/field candidates only.
```

## Manual WeChat Store

For high-confidence investment research use, save important official-account articles locally and set:

```bash
export MANUAL_WECHAT_ROOT="/Users/chen/macro-strategy/manual_wechat_articles"
```

The adapter reads `.md`, `.json`, and `.jsonl` files recursively.

Markdown format:

```markdown
---
title: "文章标题"
url: "https://mp.weixin.qq.com/s/..."
published_at: "2026-06-11"
account_name: "一凌策略研究"
---
正文或摘录
```

JSON format:

```json
[
  {
    "title": "文章标题",
    "url": "https://mp.weixin.qq.com/s/...",
    "snippet": "摘要",
    "content": "正文",
    "published_at": "2026-06-11",
    "account_name": "一凌策略研究"
  }
]
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
manual wechat directory not found
manual wechat returned no matching articles
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
