# WeChat GZH Success Playbook

This project should use `tools/gzh_fetch.py` as the default automated path for WeChat official-account articles.

## What Worked

The successful production path is:

```text
DAJIALA_KEY + accounts.json
  -> tools/gzh_fetch.py
  -> WECHAT_OPENCLI_COMMAND
  -> ir_search --source wechat
```

Example:

```bash
export DAJIALA_KEY="..."
export WECHAT_OPENCLI_COMMAND="python3 /Users/chen/Documents/Codex/2026-06-08/files-mentioned-by-the-user-ir/tools/gzh_fetch.py --accounts /Users/chen/Documents/Codex/2026-06-08/files-mentioned-by-the-user-ir/accounts.json --opencli --providers dajiala,wewe,rss --default-days 30"
python3 -m ir_search "一凌策略研究 最新文章" --source wechat --count 2
```

For `一凌策略研究`, this returned the latest two articles:

```text
2026-06-11 11:38  直到尽头丨牟一凌在国金证券2026年中期策略会的演讲
2026-06-10 11:41  直到尽头 | 国金证券2026年中期策略展望
```

The result came from `dajiala`; `wewe` and `rss` were skipped because their account locators were not configured yet.

## Provider Roles

Use the three providers as independent retrieval sources:

```text
dajiala -> discovery/list API for latest article metadata and mp.weixin URLs
wewe    -> self-hosted wewe-rss / we-mp-rss feed, usually best when full-text feed is enabled
rss     -> generic RSS/Atom source, including RSSHub or any direct feed URL
```

`wewe-rss` credentials stay inside its own service. The user scans QR in the wewe-rss management UI. This project only reads local feed output such as:

```text
http://127.0.0.1:4000/feeds/<mp_id>.json
```

Do not put WeChat Reading account/password into this project.

## Link vs Content

Article discovery still needs at least one provider. In practice:

```text
latest article list / article links -> dajiala, wewe, or rss
article body priority                -> provider feed/list content -> direct mp.weixin -> dajiala detail fallback
```

For the latest two `一凌策略研究` articles, direct full-text extraction from the returned mp.weixin URLs worked:

```text
直到尽头丨牟一凌在国金证券2026年中期策略会的演讲 -> about 3596 chars
直到尽头 | 国金证券2026年中期策略展望 -> about 2474 chars
```

So we do not need to pay for a separate provider detail API just to read body text when the mp.weixin URL is reachable. Keep `dajiala` for discovery and use `--fulltext` as the body fallback.

Quality-first fallback policy:

```text
1. If wewe-rss / RSS / dajiala list already provides content, keep it and record content_source.
2. If content is missing, fetch the returned mp.weixin URL directly.
3. If direct mp.weixin extraction fails or returns empty content, call dajiala detail API as the last resort.
4. Preserve content_source and content_errors so downstream research can audit where the body came from.
```

The detail API is deliberately last because it may add cost. It is there for data quality, not as the default cheap path.

Relevant fields in output:

```json
{
  "content": "...",
  "content_source": "mp.weixin | wewe | rss | dajiala | dajiala_detail",
  "content_errors": ["mp.weixin: RuntimeError: blocked"]
}
```

## Cross-Check Output

The core quality artifact is `crosscheck`:

```json
{
  "per_source_counts": {"dajiala": 11},
  "union": 11,
  "matrix": [],
  "only_in_one_source": [],
  "source_errors": {
    "wewe": "accounts 配置缺该路定位符，跳过",
    "rss": "accounts 配置缺该路定位符，跳过"
  }
}
```

When more providers are configured, inspect:

```text
per_source_counts    -> each provider's recall
matrix               -> which providers found each article
only_in_one_source   -> candidates needing extra review
source_errors        -> broken or unconfigured sources
```

## Operational Defaults

Recommended default command:

```bash
export WECHAT_OPENCLI_COMMAND="python3 /Users/chen/Documents/Codex/2026-06-08/files-mentioned-by-the-user-ir/tools/gzh_fetch.py --accounts /Users/chen/Documents/Codex/2026-06-08/files-mentioned-by-the-user-ir/accounts.json --opencli --providers dajiala,wewe,rss --default-days 30"
```

Recommended full-text diagnostic:

```bash
python3 tools/gzh_fetch.py \
  --accounts accounts.json \
  --account "一凌策略研究" \
  --start 2026-06-10 \
  --end 2026-06-12 \
  --providers dajiala \
  --fulltext \
  --out work/yiling_latest_fulltext_check.json
```

## Known VERIFY Point

`tools/gzh_fetch.py` marks `DAJIALA_ENDPOINT`, `DAJIALA_DETAIL_ENDPOINT`, and field mapping as `VERIFY`. If 极致了 changes its console API shape, update those constants/field candidates first; the merge, dedup, cross-check, full-text fallback, and OpenCLI mapping logic should not need to change.
