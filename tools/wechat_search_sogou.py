#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


DEFAULT_ENDPOINT = "https://weixin.sogou.com/weixin"


def main() -> int:
    args = parse_args()
    try:
        rows = search_sogou_wechat(args.query, count=args.count, timeout=args.timeout)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(rows, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def search_sogou_wechat(query: str, count: int, timeout: int) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({"type": "2", "query": query, "ie": "utf8"})
    endpoint = os.environ.get("WECHAT_SOGOU_ENDPOINT", DEFAULT_ENDPOINT)
    url = f"{endpoint}?{params}"
    headers = {
        "User-Agent": os.environ.get(
            "WECHAT_SOGOU_USER_AGENT",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
        ),
        "Accept": "text/html,application/xhtml+xml",
    }
    cookie = os.environ.get("WECHAT_SOGOU_COOKIE")
    if cookie:
        headers["Cookie"] = cookie

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    return parse_sogou_html(body, count=count)


def parse_sogou_html(body: str, count: int) -> list[dict[str, Any]]:
    if "请输入验证码" in body or "antispider" in body.lower():
        raise RuntimeError("sogou wechat returned anti-spider verification")

    rows: list[dict[str, Any]] = []
    blocks = re.findall(r"<li\b[^>]*>(.*?)</li>", body, flags=re.I | re.S)
    for block in blocks:
        title = clean_html(first_match(block, r"<h3\b[^>]*>.*?<a\b[^>]*>(.*?)</a>.*?</h3>"))
        href = first_match(block, r"<h3\b[^>]*>.*?<a\b[^>]*href=[\"']([^\"']+)[\"']")
        snippet = clean_html(first_match(block, r"<p\b[^>]*class=[\"']txt-info[\"'][^>]*>(.*?)</p>"))
        account_name = clean_html(first_match(block, r"<a\b[^>]*class=[\"']account[\"'][^>]*>(.*?)</a>"))
        if not title or not href:
            continue
        rows.append(
            {
                "title": title,
                "url": normalize_url(href),
                "snippet": snippet,
                "published_at": "",
                "account_name": account_name,
                "source_note": "sogou_wechat_public_search_candidate",
            }
        )
        if len(rows) >= count:
            break

    if rows:
        return rows

    for href in re.findall(r"https?://mp\.weixin\.qq\.com/[^\"'<>\\\s]+", html.unescape(body)):
        rows.append(
            {
                "title": href,
                "url": normalize_url(href),
                "snippet": "",
                "published_at": "",
                "account_name": "",
                "source_note": "sogou_wechat_public_search_candidate_url_only",
            }
        )
        if len(rows) >= count:
            break
    return rows


def normalize_url(value: str) -> str:
    unescaped = html.unescape(value)
    if unescaped.startswith("//"):
        return "https:" + unescaped
    if unescaped.startswith("/"):
        return urllib.parse.urljoin(DEFAULT_ENDPOINT, unescaped)
    return unescaped


def clean_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", "", value or "")
    return html.unescape(text).replace("\xa0", " ").strip()


def first_match(text: str, pattern: str) -> str:
    match = re.search(pattern, text or "", flags=re.I | re.S)
    return match.group(1) if match else ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search public WeChat article candidates through Sogou Weixin.")
    parser.add_argument("--json", action="store_true", help="Accepted for WECHAT_OPENCLI_COMMAND compatibility.")
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--count", type=int, default=int(os.environ.get("WECHAT_SOGOU_COUNT", "5")))
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("WECHAT_SOGOU_TIMEOUT", "20")))
    parser.add_argument("query")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
