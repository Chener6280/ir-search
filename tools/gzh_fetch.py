#!/usr/bin/env python3
"""
gzh_fetch.py — 读取某公众号在指定日期范围内的文章及正文（三路 cross-check）

三个 provider（可单独开关）：
  dajiala   极致了数据 API（需 env DAJIALA_KEY；endpoint/字段映射见下方 VERIFY 标注）
  wewe      自建 wewe-rss 服务（微信读书小号扫码登录在 wewe-rss 管理端完成，
            本工具只读其本地 JSON Feed，不接触任何凭据）
  rss       通用 RSS/Atom 源（RSSHub 路由或任意 feed URL）

账号映射文件 accounts.json（每个公众号一条，三路各自的定位符）：
  {
    "郭磊宏观茶座": {
      "dajiala": {"name": "郭磊宏观茶座"},          # 或 {"biz": "Mzxxxx=="}
      "wewe":    {"mp_id": "MP_WXS_xxxx"},          # wewe-rss 里该号的 feed id
      "rss":     {"url": "https://rsshub.app/wechat/..."}
    }
  }

用法：
  python3 gzh_fetch.py --accounts accounts.json --account 郭磊宏观茶座 \
      --start 2026-06-01 --end 2026-06-07 \
      --providers dajiala,wewe,rss --fulltext --out result.json

  python3 gzh_fetch.py --selftest          # 离线自测（不联网）

输出（stdout 或 --out 文件）：JSON 对象
  {
    "articles": [   # 合并去重后的文章，兼容 WECHAT_OPENCLI_COMMAND 契约字段
      {"title", "url", "snippet", "published_at", "account_name",
       "content", "found_in": ["dajiala","wewe"], "url_key": "..."}
    ],
    "crosscheck": {
      "per_source_counts": {"dajiala": 8, "wewe": 7, "rss": 6},
      "union": 9,
      "matrix": [ {"title","published_at","dajiala":true,"wewe":true,"rss":false} ],
      "only_in_one_source": [...],     # 仅单源命中的（重点人工核对对象）
      "source_errors": {"rss": "..."}  # 某路失败不影响其余两路
    }
  }

仅用标准库；日志走 stderr，stdout 只输出 JSON。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, date, timezone, timedelta
from html.parser import HTMLParser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from ir_search.urlnorm import wechat_url_key

CST = timezone(timedelta(hours=8))  # 公众号时间一律按北京时间处理
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def log(*a):
    print(*a, file=sys.stderr)


# --------------------------------------------------------------------------
# 数据模型与 URL 规范化（cross-check 的去重键）
# --------------------------------------------------------------------------

@dataclass
class Article:
    title: str
    url: str
    published_at: datetime | None
    snippet: str = ""
    content: str = ""
    account_name: str = ""
    source: str = ""               # 本条来自哪个 provider
    url_key: str = field(default="", compare=False)

    def __post_init__(self):
        if not self.url_key:
            self.url_key = normalize_wechat_url(self.url, self.title, self.published_at)


def normalize_wechat_url(url: str, title: str = "", published: datetime | None = None) -> str:
    """mp.weixin 链接的规范化键：
    优先 sn 参数（同一篇文章跨渠道 sn 一致）；否则 (biz, mid, idx)；
    短链 /s/<token> 用 token；都没有则 title+日期 哈希兜底。"""
    return wechat_url_key(url, title=title, published=published)


def parse_dt(v) -> datetime | None:
    """容错时间解析：unix 秒/毫秒、ISO8601、'YYYY-MM-DD HH:MM(:SS)'、'YYYY-MM-DD'。"""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
        ts = float(v)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, CST)
    s = str(v).strip()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(CST)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d",
                "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=CST)
        except ValueError:
            continue
    return None


def in_window(a: Article, start: date, end: date) -> bool:
    if a.published_at is None:
        return False                     # 没有日期的不进窗口（宁缺勿混）
    d = a.published_at.astimezone(CST).date()
    return start <= d <= end


# --------------------------------------------------------------------------
# HTTP 小工具
# --------------------------------------------------------------------------

def http_json(url: str, payload: dict | None = None, timeout: int = 30):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method="POST" if data else "GET",
        headers={"User-Agent": UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def http_text(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def dig(obj, *paths):
    """从多个候选路径里取第一个非空值，如 dig(d, 'data.list', 'data.data', 'list')。"""
    for path in paths:
        cur = obj
        ok = True
        for k in path.split("."):
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                ok = False
                break
        if ok and cur not in (None, "", []):
            return cur
    return None


# --------------------------------------------------------------------------
# Provider 1: 极致了 dajiala
# --------------------------------------------------------------------------
# !!! VERIFY：以下 endpoint 与字段名按社区常见形态实现，登录控制台核对文档后，
#     如有出入只需改这几个常量/键名，不必动逻辑。
DAJIALA_ENDPOINT = os.environ.get(
    "DAJIALA_ENDPOINT", "https://www.dajiala.com/fbmain/monitor/v3/post_history")
DAJIALA_DETAIL_ENDPOINT = os.environ.get(
    "DAJIALA_DETAIL_ENDPOINT", "https://www.dajiala.com/fbmain/monitor/v3/post_detail")
DAJIALA_MAX_PAGES = int(os.environ.get("DAJIALA_MAX_PAGES", "3"))
DAJIALA_DETAIL_ENABLED = os.environ.get("DAJIALA_DETAIL_ENABLED", "1") not in {"0", "false", "False"}


def fetch_dajiala(acct_cfg: dict, account_name: str,
                  start: date, end: date) -> list[Article]:
    key = os.environ.get("DAJIALA_KEY")
    if not key:
        raise RuntimeError("env DAJIALA_KEY 未设置")
    out: list[Article] = []
    for page in range(1, DAJIALA_MAX_PAGES + 1):
        payload = {"key": key, "page": page}
        # 定位符：biz 优先（精确），否则名称
        if acct_cfg.get("biz"):
            payload["biz"] = acct_cfg["biz"]
        elif acct_cfg.get("name"):
            payload["name"] = acct_cfg["name"]
        else:
            raise RuntimeError("dajiala 配置需含 biz 或 name")
        resp = http_json(DAJIALA_ENDPOINT, payload)
        rows = dig(resp, "data.list", "data.data", "data", "list") or []
        if not isinstance(rows, list) or not rows:
            break
        page_oldest: datetime | None = None
        for r in rows:
            if not isinstance(r, dict):
                continue
            pub = parse_dt(r.get("post_time") or r.get("post_time_str")
                           or r.get("publish_time") or r.get("time"))
            a = Article(
                title=(r.get("title") or "").strip(),
                url=r.get("url") or r.get("link") or "",
                published_at=pub,
                snippet=(r.get("digest") or r.get("summary") or "").strip(),
                content=(r.get("content") or "").strip(),   # 列表接口多半无正文
                account_name=account_name, source="dajiala")
            if a.title and a.url:
                out.append(a)
            if pub and (page_oldest is None or pub < page_oldest):
                page_oldest = pub
        # 翻页早停：本页最旧一条已早于窗口起点，则后页更旧，停止（省调用费）
        if page_oldest and page_oldest.date() < start:
            break
    return [a for a in out if in_window(a, start, end)]


def fetch_dajiala_detail(article: dict) -> str:
    """Last-resort body fetch through 极致了 detail API.

    VERIFY: endpoint, payload keys, and response field paths must be checked
    against the 极致了 console docs. The surrounding fallback logic is stable;
    if the API shape differs, update this function's constants/candidate paths.
    """
    if not DAJIALA_DETAIL_ENABLED:
        raise RuntimeError("DAJIALA_DETAIL_ENABLED=0")
    key = os.environ.get("DAJIALA_KEY")
    if not key:
        raise RuntimeError("env DAJIALA_KEY 未设置")
    payload = {
        "key": key,
        "url": article.get("url") or "",
        "url_key": article.get("url_key") or "",
        "title": article.get("title") or "",
    }
    resp = http_json(DAJIALA_DETAIL_ENDPOINT, payload)
    content = dig(
        resp,
        "data.content",
        "data.html",
        "data.text",
        "data.article.content",
        "data.article.html",
        "content",
        "html",
        "text",
    )
    if not content:
        raise RuntimeError("dajiala detail returned no content")
    return strip_html(str(content))


# --------------------------------------------------------------------------
# Provider 2: 自建 wewe-rss（微信读书小号在其管理端扫码，一次性）
# --------------------------------------------------------------------------
WEWE_BASE = os.environ.get("WEWE_RSS_BASE", "http://127.0.0.1:4000")


def fetch_wewe(acct_cfg: dict, account_name: str,
               start: date, end: date) -> list[Article]:
    mp_id = acct_cfg.get("mp_id")
    if not mp_id:
        raise RuntimeError("wewe 配置需含 mp_id（wewe-rss 中该号的 feed id）")
    # wewe-rss 提供 JSON Feed： {base}/feeds/{mp_id}.json （开启全文输出时 content_html 含正文）
    feed = http_json(f"{WEWE_BASE.rstrip('/')}/feeds/{mp_id}.json")
    out: list[Article] = []
    for it in feed.get("items", []):
        a = Article(
            title=(it.get("title") or "").strip(),
            url=it.get("url") or it.get("external_url") or "",
            published_at=parse_dt(it.get("date_published") or it.get("date_modified")),
            snippet=strip_html(it.get("summary") or "")[:200],
            content=strip_html(it.get("content_html") or it.get("content_text") or ""),
            account_name=account_name, source="wewe")
        if a.title and a.url:
            out.append(a)
    return [a for a in out if in_window(a, start, end)]


# --------------------------------------------------------------------------
# Provider 3: 通用 RSS/Atom（RSSHub 路由或任意 feed URL）
# --------------------------------------------------------------------------

def fetch_rss(acct_cfg: dict, account_name: str,
              start: date, end: date) -> list[Article]:
    url = acct_cfg.get("url")
    if not url:
        raise RuntimeError("rss 配置需含 url")
    xml = http_text(url)
    out = [a for a in parse_feed_xml(xml, account_name) if in_window(a, start, end)]
    return out


def parse_feed_xml(xml: str, account_name: str) -> list[Article]:
    """无依赖的 RSS2/Atom 解析（容错，按 item/entry 切块 + 正则取字段）。"""
    items = re.findall(r"<(?:item|entry)\b.*?</(?:item|entry)>", xml, re.S | re.I)
    out: list[Article] = []
    for blk in items:
        def tag(name):
            m = re.search(rf"<{name}\b[^>]*>(.*?)</{name}>", blk, re.S | re.I)
            return unescape_xml(m.group(1).strip()) if m else ""
        title = strip_html(tag("title"))
        link = tag("link")
        if not link:  # Atom: <link href="..."/>
            m = re.search(r"<link\b[^>]*href=\"([^\"]+)\"", blk, re.I)
            link = unescape_xml(m.group(1)) if m else ""
        pub = parse_dt(tag("pubDate") or tag("published") or tag("updated") or tag("dc:date"))
        content = strip_html(tag("content:encoded") or tag("content") or "")
        snippet = strip_html(tag("description") or tag("summary"))[:200]
        if title and link:
            out.append(Article(title=title, url=link, published_at=pub,
                               snippet=snippet, content=content,
                               account_name=account_name, source="rss"))
    return out


def unescape_xml(s: str) -> str:
    s = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", s, flags=re.S)
    for a, b in [("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'),
                 ("&#39;", "'"), ("&amp;", "&")]:
        s = s.replace(a, b)
    return s


# --------------------------------------------------------------------------
# 正文：feed/接口没给全文时，直接抓 mp.weixin 文章页提取 #js_content
# --------------------------------------------------------------------------

class _TextExtract(HTMLParser):
    def __init__(self):
        super().__init__()
        self.buf: list[str] = []
        self.skip = 0

    def handle_starttag(self, t, attrs):
        if t in ("script", "style"):
            self.skip += 1
        if t in ("p", "br", "div", "section", "li"):
            self.buf.append("\n")

    def handle_endtag(self, t):
        if t in ("script", "style") and self.skip:
            self.skip -= 1

    def handle_data(self, d):
        if not self.skip:
            self.buf.append(d)


def strip_html(html: str) -> str:
    p = _TextExtract()
    try:
        p.feed(html or "")
    except Exception:
        return re.sub(r"<[^>]+>", " ", html or "")
    txt = "".join(p.buf)
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", txt)).strip()


def fetch_fulltext(url: str) -> str:
    html = http_text(url)
    m = re.search(r"id=\"js_content\"[^>]*>(.*?)</div>\s*<", html, re.S)
    body = m.group(1) if m else html
    return strip_html(body)


# --------------------------------------------------------------------------
# 合并 + cross-check 矩阵
# --------------------------------------------------------------------------

PROVIDERS = {"dajiala": fetch_dajiala, "wewe": fetch_wewe, "rss": fetch_rss}
# 字段优先级：正文以 wewe(全文feed) 优先，元数据以 dajiala 优先
CONTENT_PRIORITY = ["wewe", "dajiala", "rss"]
META_PRIORITY = ["dajiala", "wewe", "rss"]


def merge(per_source: dict[str, list[Article]]):
    by_key: dict[str, dict[str, Article]] = {}
    for src, arts in per_source.items():
        for a in arts:
            by_key.setdefault(a.url_key, {})[src] = a

    merged, matrix = [], []
    for key, variants in by_key.items():
        meta = next((variants[s] for s in META_PRIORITY if s in variants),
                    next(iter(variants.values())))
        content = next((variants[s].content for s in CONTENT_PRIORITY
                        if s in variants and variants[s].content), "")
        content_source = next((s for s in CONTENT_PRIORITY
                               if s in variants and variants[s].content), "")
        snippet = next((variants[s].snippet for s in META_PRIORITY
                        if s in variants and variants[s].snippet), "")
        merged.append({
            "title": meta.title, "url": meta.url,
            "snippet": snippet,
            "published_at": meta.published_at.astimezone(CST).strftime("%Y-%m-%d %H:%M")
                            if meta.published_at else "",
            "account_name": meta.account_name,
            "content": content,
            "content_source": content_source,
            "content_errors": [],
            "found_in": sorted(variants.keys()),
            "url_key": key,
        })
        matrix.append({"title": meta.title,
                       "published_at": merged[-1]["published_at"],
                       **{s: (s in variants) for s in per_source}})
    merged.sort(key=lambda x: x["published_at"], reverse=True)
    matrix.sort(key=lambda x: x["published_at"], reverse=True)
    return merged, matrix


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def run(accounts_path, account, start, end, providers, want_fulltext, out_path=None, emit=True):
    accounts = json.load(open(accounts_path, encoding="utf-8"))
    if account not in accounts:
        raise SystemExit(f"accounts 文件中无该公众号: {account}")
    cfg = accounts[account]

    per_source, errors = {}, {}
    for src in providers:
        if src not in PROVIDERS:
            errors[src] = "未知 provider"
            continue
        if src not in cfg:
            errors[src] = "accounts 配置缺该路定位符，跳过"
            continue
        try:
            per_source[src] = PROVIDERS[src](cfg[src], account, start, end)
            log(f"[{src}] {len(per_source[src])} 篇（窗口内）")
        except Exception as e:                      # 单路失败不影响其余
            errors[src] = f"{type(e).__name__}: {e}"
            log(f"[{src}] 失败: {errors[src]}")

    merged, matrix = merge(per_source)

    if want_fulltext:
        for art in merged:
            fill_missing_content(art)

    only_one = [m["title"] for m in matrix
                if sum(1 for s in per_source if m.get(s)) == 1]
    result = {
        "articles": merged,
        "crosscheck": {
            "per_source_counts": {s: len(v) for s, v in per_source.items()},
            "union": len(merged),
            "matrix": matrix,
            "only_in_one_source": only_one,
            "source_errors": errors,
        },
    }
    if not emit:
        return result
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if out_path:
        open(out_path, "w", encoding="utf-8").write(text)
        log(f"已写出 {out_path}")
    else:
        print(text)
    return result


def fill_missing_content(article: dict) -> None:
    if article.get("content"):
        return
    errors: list[str] = article.setdefault("content_errors", [])

    try:
        content = fetch_fulltext(article["url"])
        if not content:
            raise RuntimeError("mp.weixin returned empty content")
        article["content"] = content
        article["content_source"] = "mp.weixin"
        log(f"[fulltext] 抓取 {article['title'][:24]}… {len(article['content'])} 字")
        return
    except Exception as exc:
        errors.append(f"mp.weixin: {type(exc).__name__}: {exc}")
        log(f"[fulltext] 失败 {article.get('url')}: {exc}")

    try:
        content = fetch_dajiala_detail(article)
        if not content:
            raise RuntimeError("dajiala detail returned empty content")
        article["content"] = content
        article["content_source"] = "dajiala_detail"
        log(f"[dajiala_detail] 兜底 {article['title'][:24]}… {len(article['content'])} 字")
    except Exception as exc:
        errors.append(f"dajiala_detail: {type(exc).__name__}: {exc}")
        log(f"[dajiala_detail] 失败 {article.get('url')}: {exc}")


def opencli_rows(result: dict) -> list[dict]:
    """Map the full cross-check object to WECHAT_OPENCLI_COMMAND's JSON-list contract."""
    rows = []
    crosscheck = result.get("crosscheck") or {}
    for article in result.get("articles", []):
        if not isinstance(article, dict):
            continue
        row = {
            "title": article.get("title") or "",
            "url": article.get("url") or "",
            "snippet": article.get("snippet") or "",
            "published_at": article.get("published_at") or "",
            "account_name": article.get("account_name") or "",
            "content": article.get("content") or "",
            "content_source": article.get("content_source") or "",
            "content_errors": article.get("content_errors") or [],
            "found_in": article.get("found_in") or [],
            "url_key": article.get("url_key") or "",
            "crosscheck": crosscheck,
            "extraction_method": "gzh_crosscheck",
        }
        if row["title"] and row["url"]:
            rows.append(row)
    return rows


def infer_account(accounts_path: str, query: str) -> str:
    accounts = json.load(open(accounts_path, encoding="utf-8"))
    account_names = [name for name, cfg in accounts.items() if not name.startswith("_") and isinstance(cfg, dict)]
    matches = [name for name in account_names if name in query]
    if len(matches) == 1:
        return matches[0]
    if not matches and len(account_names) == 1:
        return account_names[0]
    if not matches:
        raise SystemExit("无法从 query 推断公众号名；请传 --account 或让 query 包含 accounts.json 中的公众号名")
    raise SystemExit(f"query 同时匹配多个公众号: {', '.join(matches)}；请传 --account")


def default_window(days: int) -> tuple[date, date]:
    end = datetime.now(CST).date()
    start = end - timedelta(days=max(1, days) - 1)
    return start, end


# --------------------------------------------------------------------------
# 离线自测：不联网，验证 URL 键、日期窗、RSS 解析、三路合并矩阵
# --------------------------------------------------------------------------

def selftest():
    ok = True

    def check(name, cond):
        nonlocal ok
        log(("PASS " if cond else "FAIL ") + name)
        ok = ok and cond

    # 1) 同一篇文章不同链接形态 → 同一 url_key
    u1 = ("https://mp.weixin.qq.com/s?__biz=MzA3&mid=2650001&idx=1"
          "&sn=abcdef0123456789abcdef01&chksm=xx")
    u2 = "https://mp.weixin.qq.com/s?sn=abcdef0123456789abcdef01&foo=bar"
    check("url_key: sn 一致即同键",
          normalize_wechat_url(u1) == normalize_wechat_url(u2))
    check("url_key: 短链 token",
          normalize_wechat_url("https://mp.weixin.qq.com/s/AbC123xyz_九").startswith(("wechat:tok:", "wechat:th:")))

    # 2) 日期窗（含 unix 秒与字符串两种来源）
    a = Article("t", "https://mp.weixin.qq.com/s/x1234567890", parse_dt(1750000000))
    check("parse_dt unix 秒", a.published_at is not None)
    w_in = Article("t", "u", parse_dt("2026-06-03 08:00"))
    w_out = Article("t", "u", parse_dt("2026-05-25"))
    s, e = date(2026, 6, 1), date(2026, 6, 7)
    check("窗口内", in_window(w_in, s, e) and not in_window(w_out, s, e))

    # 3) RSS 解析（RSS2 + Atom 混合要点）
    rss = """<rss><channel>
      <item><title><![CDATA[A股周观点]]></title>
        <link>https://mp.weixin.qq.com/s?sn=feedaaaaaaaaaaaaaaaaaaaa</link>
        <pubDate>Wed, 03 Jun 2026 09:00:00 +0800</pubDate>
        <description>本周观点摘要</description></item>
    </channel></rss>"""
    arts = parse_feed_xml(rss, "测试号")
    check("RSS 解析出 1 条且字段齐",
          len(arts) == 1 and arts[0].title == "A股周观点"
          and arts[0].published_at.date() == date(2026, 6, 3))

    # 4) 三路合并：两路同篇(sn 同) + 一路独有 → union=2，矩阵正确，正文优先级取 wewe
    sn_url = "https://mp.weixin.qq.com/s?sn=feedaaaaaaaaaaaaaaaaaaaa"
    dj = [Article("A股周观点", sn_url, parse_dt("2026-06-03 09:00"),
                  snippet="摘要dj", account_name="测试号", source="dajiala")]
    we = [Article("A股周观点", sn_url + "&from=feed", parse_dt("2026-06-03 09:01"),
                  content="全文正文……", account_name="测试号", source="wewe"),
          Article("独家加更", "https://mp.weixin.qq.com/s?sn=bbbbbbbbbbbbbbbbbbbbbbbb",
                  parse_dt("2026-06-05"), account_name="测试号", source="wewe")]
    merged, matrix = merge({"dajiala": dj, "wewe": we})
    row = next(m for m in matrix if m["title"] == "A股周观点")
    art = next(m for m in merged if m["title"] == "A股周观点")
    check("合并 union=2", len(merged) == 2)
    check("矩阵: 同篇两路均 True", row["dajiala"] and row["wewe"])
    check("正文取自 wewe，元数据摘要取自 dajiala",
          art["content"].startswith("全文") and art["snippet"] == "摘要dj")
    check("仅单源命中清单含 独家加更",
          "独家加更" in [m["title"] for m in matrix
                       if sum(1 for ss in ("dajiala", "wewe") if m.get(ss)) == 1])

    log("=== selftest", "ALL PASS" if ok else "HAS FAILURES", "===")
    return 0 if ok else 1


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--accounts", default="accounts.json")
    p.add_argument("--account", help="公众号名（accounts.json 中的键）")
    p.add_argument("--start", help="YYYY-MM-DD")
    p.add_argument("--end", help="YYYY-MM-DD")
    p.add_argument("--providers", default="dajiala,wewe,rss")
    p.add_argument("--fulltext", action="store_true",
                   help="对无正文的合并结果抓取 mp.weixin 原文页提全文")
    p.add_argument("--out", help="结果写入文件（默认打印 stdout）")
    p.add_argument("--opencli", action="store_true",
                   help="stdout 输出 JSON list，直接兼容 WECHAT_OPENCLI_COMMAND")
    p.add_argument("--default-days", type=int, default=int(os.environ.get("GZH_FETCH_DEFAULT_DAYS", "14")),
                   help="--opencli 且未传 --start/--end 时默认回看天数")
    p.add_argument("--selftest", action="store_true")
    p.add_argument("query", nargs="?", help="WECHAT_OPENCLI_COMMAND 追加的查询文本，可用于推断公众号")
    args = p.parse_args()

    if args.selftest:
        raise SystemExit(selftest())

    account = args.account or infer_account(args.accounts, args.query or "")
    if args.start and args.end:
        start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    elif args.opencli:
        start, end = default_window(args.default_days)
    else:
        p.error("--start/--end 为必填；--opencli 模式下可省略并使用 --default-days")

    result = run(args.accounts, account,
        start, end,
        [s.strip() for s in args.providers.split(",") if s.strip()],
        args.fulltext, args.out, emit=not args.opencli)
    if args.opencli:
        text = json.dumps(opencli_rows(result), ensure_ascii=False, indent=2)
        if args.out:
            open(args.out, "w", encoding="utf-8").write(text)
            log(f"已写出 {args.out}")
        else:
            print(text)


if __name__ == "__main__":
    main()
