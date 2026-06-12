from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

from ir_search.adapters.base import AdapterError
from ir_search.adapters.wechat_dates import parse_wechat_published_at
from ir_search.models import EvidenceType, Hit, Query, SourceTier


GENERIC_QUERY_TERMS = {
    "公众号",
    "微信",
    "wechat",
    "最新",
    "文章",
    "最新文章",
    "研究",
    "策略",
}


class ManualWechatAdapter:
    name = "manual_wechat"
    mode = "live"

    def __init__(self, root: Optional[str | Path] = None) -> None:
        self.root = Path(root).expanduser() if root else manual_wechat_root()

    def query(self, q: Query) -> list[Hit]:
        if not self.root.exists():
            raise AdapterError(f"manual wechat directory not found: {self.root}")
        if not self.root.is_dir():
            raise AdapterError(f"manual wechat root is not a directory: {self.root}")

        rows = list(iter_manual_rows(self.root))
        hits = rows_to_hits(rows)
        matches = [hit for hit in hits if article_matches_query(hit, q.text)]
        if not matches and not query_terms(q.text):
            matches = hits
        matches.sort(key=lambda hit: hit.published_at or hit.fetched_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        if not matches:
            raise AdapterError(f"manual wechat returned no matching articles from {self.root}")
        return matches[: q.count]


def manual_wechat_root() -> Path:
    configured = os.environ.get("MANUAL_WECHAT_ROOT") or os.environ.get("IR_SEARCH_MANUAL_WECHAT_ROOT")
    if configured:
        return Path(configured).expanduser()
    return Path.cwd() / "manual_wechat_articles"


def iter_manual_rows(root: Path) -> Iterable[dict[str, Any]]:
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() == ".json":
            yield from parse_json_file(path)
        elif path.suffix.lower() == ".jsonl":
            yield from parse_jsonl_file(path)
        elif path.suffix.lower() in {".md", ".markdown"}:
            yield parse_markdown_file(path)


def parse_json_file(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("articles") or data.get("hits") or data.get("items") or [data]
    else:
        rows = []
    return [_with_path(row, path) for row in rows if isinstance(row, dict)]


def parse_jsonl_file(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if isinstance(row, dict):
            rows.append(_with_path(row, path))
    return rows


def parse_markdown_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    metadata, body = split_front_matter(text)
    row = dict(metadata)
    row.setdefault("snippet", body.strip()[:800])
    row.setdefault("content", body.strip())
    return _with_path(row, path)


def split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    for idx, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            metadata = yaml.safe_load("\n".join(lines[1:idx])) or {}
            body = "\n".join(lines[idx + 1 :])
            return metadata if isinstance(metadata, dict) else {}, body
    return {}, text


def rows_to_hits(rows: Iterable[dict[str, Any]]) -> list[Hit]:
    hits: list[Hit] = []
    for row in rows:
        title = str(row.get("title") or "").strip()
        url = str(row.get("url") or row.get("original_url") or "").strip()
        if not title or not url:
            continue
        content = str(row.get("content") or row.get("text") or row.get("body") or "")
        snippet = str(row.get("snippet") or content[:800] or "")
        account_name = row.get("account_name") or row.get("account") or row.get("official_account")
        published_at = parse_wechat_published_at(str(row.get("published_at") or "")) if row.get("published_at") else None
        hits.append(
            Hit(
                title=title,
                url=url,
                snippet=snippet,
                source=ManualWechatAdapter.name,
                tier=SourceTier.MEDIA,
                evidence_type=EvidenceType.OPINION,
                published_at=published_at,
                extra={
                    "platform": "wechat",
                    "account_name": account_name,
                    "content": content,
                    "content_path": row.get("_path"),
                    "extraction_method": "manual_wechat_local_file",
                    "requires_login": False,
                },
            )
        )
    return hits


def article_matches_query(hit: Hit, query: str) -> bool:
    terms = query_terms(query)
    if not terms:
        return True
    haystack = " ".join(
        [
            hit.title,
            hit.snippet,
            str(hit.extra.get("account_name") or ""),
            str(hit.extra.get("content") or "")[:5000],
        ]
    ).lower()
    return any(term.lower() in haystack for term in terms)


def query_terms(query: str) -> list[str]:
    normalized = query.replace("|", " ").replace(",", " ").replace("，", " ")
    terms = [term.strip() for term in normalized.split() if term.strip()]
    useful = [term for term in terms if term.lower() not in GENERIC_QUERY_TERMS]
    if useful:
        return useful
    compact = "".join(ch for ch in query if not ch.isspace())
    for generic in GENERIC_QUERY_TERMS:
        compact = compact.replace(generic, "")
    return [compact] if compact else []


def _with_path(row: dict[str, Any], path: Path) -> dict[str, Any]:
    copied = dict(row)
    copied["_path"] = str(path)
    return copied
