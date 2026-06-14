from __future__ import annotations

import hashlib
import re
from datetime import datetime
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse


def canonicalize_url(url: str, title: str = "", published: datetime | None = None) -> str:
    parsed = urlparse(url)
    domain = parsed.netloc.lower().removeprefix("www.")
    if domain == "mp.weixin.qq.com":
        return wechat_url_key(url, title=title, published=published)

    query = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if not (k.lower().startswith("utm_") or k.lower() in {"spm", "from", "share"})
    ]
    path = parsed.path.rstrip("/") or "/"
    return urlunparse(("", domain, path, "", urlencode(query), ""))


def canonicalize_url_aliases(url: str, title: str = "", published: datetime | None = None) -> list[str]:
    parsed = urlparse(url)
    domain = parsed.netloc.lower().removeprefix("www.")
    if domain == "mp.weixin.qq.com":
        return wechat_url_aliases(url, title=title, published=published)
    return [canonicalize_url(url, title=title, published=published)]


def wechat_url_key(url: str, title: str = "", published: datetime | None = None) -> str:
    return wechat_url_aliases(url, title=title, published=published)[0]


def wechat_url_aliases(url: str, title: str = "", published: datetime | None = None) -> list[str]:
    parsed = urlparse(url or "")
    domain = parsed.netloc.lower().removeprefix("www.")
    query = parse_qs(parsed.query)
    aliases: list[str] = []
    if query.get("sn"):
        aliases.append("wechat:sn:" + query["sn"][0])
    if query.get("__biz") and query.get("mid"):
        idx = (query.get("idx") or ["1"])[0]
        aliases.append(f"wechat:bmi:{query['__biz'][0]}:{query['mid'][0]}:{idx}")
    if aliases:
        return aliases

    match = re.match(r"^/s/([A-Za-z0-9_-]{10,})$", parsed.path or "")
    if domain == "mp.weixin.qq.com" and match:
        return ["wechat:tok:" + match.group(1)]

    day = published.strftime("%Y-%m-%d") if published else ""
    digest = hashlib.sha1(f"{(title or '').strip()}|{day}".encode("utf-8")).hexdigest()[:16]
    return ["wechat:th:" + digest]
