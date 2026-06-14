#!/usr/bin/env python3
"""Discover WeChat article links and optionally add them to a local wewe-rss.

The tool intentionally keeps credentials outside code:

1. DAJIALA_KEY is read from env / ir_search.env and used only to discover
   a recent mp.weixin article link for each account name.
2. wewe-rss uses its own scanned WeChat Reading session. This tool only calls
   the local wewe-rss HTTP API with AUTH_CODE.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import gzh_fetch  # noqa: E402


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def read_names(path: Path) -> list[str]:
    names: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            names.append(line)
    return names


def newest_article_url(name: str, start: date, end: date) -> tuple[str, dict[str, Any] | None, str | None]:
    try:
        rows = gzh_fetch.fetch_dajiala({"name": name}, name, start, end)
    except Exception as exc:  # provider errors are reported per-account
        return name, None, str(exc)
    if not rows:
        return name, None, "no articles in window"
    rows = sorted(rows, key=lambda item: item.published_at or gzh_fetch.parse_dt("1970-01-01"), reverse=True)
    first = rows[0]
    return name, {
        "account": name,
        "title": first.title,
        "url": first.url,
        "published_at": first.published_at.isoformat() if first.published_at else None,
        "snippet": first.snippet,
    }, None


def discover_links(args: argparse.Namespace) -> dict[str, Any]:
    load_env(Path(args.env))
    gzh_fetch.DAJIALA_MAX_PAGES = args.max_pages
    names = read_names(Path(args.names))
    end = date.fromisoformat(args.end) if args.end else date.today()
    start = date.fromisoformat(args.start) if args.start else end - timedelta(days=args.days)

    found: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    for idx, name in enumerate(names, 1):
        print(f"[{idx}/{len(names)}] {name}", file=sys.stderr)
        _, row, error = newest_article_url(name, start, end)
        if row and row.get("url"):
            found.append(row)
        else:
            missing.append({"account": name, "error": error or "unknown error"})
        if args.sleep:
            time.sleep(args.sleep)

    result = {
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "counts": {"requested": len(names), "found": len(found), "missing": len(missing)},
        "found": found,
        "missing": missing,
    }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "wechat_wewe_discovery.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "wechat_wewe_links.txt").write_text(
        "\n".join(row["url"] for row in found if row.get("url")) + ("\n" if found else ""),
        encoding="utf-8",
    )
    return result


def trpc_post(base_url: str, path: str, payload: dict[str, Any], auth_code: str) -> Any:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/trpc/{path}",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": auth_code},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{path} HTTP {exc.code}: {detail}") from exc
    return unwrap_trpc(data)


def unwrap_trpc(data: Any) -> Any:
    if isinstance(data, list):
        if len(data) != 1:
            return data
        data = data[0]
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(data["error"].get("message") or json.dumps(data["error"], ensure_ascii=False))
    if not isinstance(data, dict) or "result" not in data:
        return data
    result = data["result"]
    if not isinstance(result, dict) or "data" not in result:
        return result
    inner = result["data"]
    if isinstance(inner, dict) and "json" in inner:
        return inner["json"]
    if isinstance(inner, list) and len(inner) == 1:
        return inner[0]
    return inner


def add_to_wewe(args: argparse.Namespace) -> dict[str, Any]:
    links_path = Path(args.links)
    links = [line.strip() for line in links_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    added: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    for idx, link in enumerate(links, 1):
        print(f"[{idx}/{len(links)}] {link}", file=sys.stderr)
        try:
            info = trpc_post(args.wewe_base, "platform.getMpInfo", {"wxsLink": link}, args.auth_code)
            row = {
                "id": str(info["id"]),
                "mpName": info.get("name") or info.get("mpName") or "",
                "mpCover": info.get("cover") or "",
                "mpIntro": info.get("intro") or "",
                "updateTime": info.get("updateTime") or 0,
                "status": 1,
            }
            trpc_post(args.wewe_base, "feed.add", row, args.auth_code)
            added.append({"url": link, **row})
        except Exception as exc:
            failed.append({"url": link, "error": str(exc)})
        if args.sleep:
            time.sleep(args.sleep)

    result = {"counts": {"links": len(links), "added": len(added), "failed": len(failed)}, "added": added, "failed": failed}
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "wechat_wewe_add_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    discover = sub.add_parser("discover")
    discover.add_argument("--names", default="configs/wechat_research_accounts.txt")
    discover.add_argument("--env", default="ir_search.env")
    discover.add_argument("--out-dir", default="work")
    discover.add_argument("--days", type=int, default=90)
    discover.add_argument("--start")
    discover.add_argument("--end")
    discover.add_argument("--max-pages", type=int, default=1)
    discover.add_argument("--sleep", type=float, default=0.2)

    add = sub.add_parser("add-wewe")
    add.add_argument("--links", default="work/wechat_wewe_links.txt")
    add.add_argument("--wewe-base", default="http://localhost:4001")
    add.add_argument("--auth-code", default="irsearch")
    add.add_argument("--out-dir", default="work")
    add.add_argument("--sleep", type=float, default=2.5)

    args = parser.parse_args()
    if args.cmd == "discover":
        result = discover_links(args)
    else:
        result = add_to_wewe(args)
    print(json.dumps(result["counts"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
