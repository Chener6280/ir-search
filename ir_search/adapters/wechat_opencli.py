from __future__ import annotations

import json
import os
import shlex
import subprocess
from datetime import datetime
from typing import Any, Optional

from ir_search.adapters.base import AdapterError
from ir_search.adapters.manual_wechat import ManualWechatAdapter
from ir_search.adapters.wechat_dates import parse_wechat_published_at
from ir_search.models import EvidenceType, Hit, Query, SourceTier


REQUIRED_FIELDS = ["title", "url"]
OPTIONAL_FIELDS = ["snippet", "published_at", "account_name"]


class WechatOpenCLIAdapter:
    name = "wechat_opencli"
    mode = "live"

    def query(self, q: Query) -> list[Hit]:
        command = os.environ.get("WECHAT_OPENCLI_COMMAND")
        if not command:
            return manual_fallback_or_raise(q, "WECHAT_OPENCLI_COMMAND is not set; browser adapter is unavailable")

        try:
            completed = subprocess.run(
                shlex.split(command) + [q.text],
                check=True,
                capture_output=True,
                text=True,
                timeout=int(os.environ.get("WECHAT_OPENCLI_TIMEOUT", "60")),
            )
        except FileNotFoundError as exc:
            return manual_fallback_or_raise(q, f"wechat opencli command not found: {exc}", retryable=True)
        except subprocess.TimeoutExpired as exc:
            return manual_fallback_or_raise(q, f"wechat opencli timed out: {exc}", retryable=True)
        except subprocess.CalledProcessError as exc:
            return manual_fallback_or_raise(q, f"wechat opencli failed: {exc.stderr or exc}", retryable=True)

        try:
            rows = _parse_stdout(completed.stdout)
        except AdapterError as exc:
            return manual_fallback_or_raise(q, str(exc), retryable=True)
        hits = rows_to_hits(rows)
        if not hits:
            return manual_fallback_or_raise(q, "wechat opencli returned no valid rows", retryable=True)
        return hits


def _parse_stdout(stdout: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise AdapterError(f"wechat opencli stdout is not JSON: {exc}", retryable=True) from exc
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict) and isinstance(data.get("articles"), list):
        crosscheck = data.get("crosscheck")
        rows = []
        for row in data["articles"]:
            if isinstance(row, dict):
                copied = dict(row)
                copied.setdefault("crosscheck", crosscheck)
                rows.append(copied)
        return rows
    raise AdapterError("wechat opencli stdout must be a JSON list or an object with articles", retryable=True)


def rows_to_hits(rows: list[dict[str, Any]]) -> list[Hit]:
    hits: list[Hit] = []
    for row in rows:
        if any(not row.get(field) for field in REQUIRED_FIELDS):
            continue

        warnings = []
        for field in OPTIONAL_FIELDS:
            if not row.get(field):
                warnings.append(f"missing_{field}")

        published_at = parse_published_at(row.get("published_at"))
        if row.get("published_at") and published_at is None:
            warnings.append("published_at_unparsed")

        extra = {
            "platform": "wechat",
            "account_name": row.get("account_name"),
            "extraction_method": row.get("extraction_method") or ("gzh_crosscheck" if row.get("found_in") else "opencli_browser"),
            "requires_login": False if row.get("found_in") else True,
        }
        for field in ["content", "content_source", "content_errors", "found_in", "url_key", "crosscheck"]:
            if row.get(field):
                extra[field] = row.get(field)
        if warnings:
            extra["parse_warning"] = ",".join(warnings)

        hits.append(
            Hit(
                title=row["title"],
                url=row["url"],
                snippet=row.get("snippet") or "",
                source=WechatOpenCLIAdapter.name,
                tier=SourceTier.MEDIA,
                evidence_type=EvidenceType.OPINION,
                published_at=published_at,
                extra=extra,
            )
        )
    return hits


def parse_published_at(value: Optional[str]) -> Optional[datetime]:
    return parse_wechat_published_at(value)


def manual_fallback_or_raise(q: Query, primary_error: str, retryable: bool = False) -> list[Hit]:
    try:
        hits = ManualWechatAdapter().query(q)
    except AdapterError as exc:
        raise AdapterError(
            f"{primary_error}; manual_wechat fallback unavailable: {exc}",
            retryable=retryable,
        ) from exc
    for hit in hits:
        hit.extra["fallback_from"] = "wechat_opencli"
        hit.extra["is_manual_wechat_fallback"] = True
        hit.extra["wechat_opencli_error"] = primary_error
    return hits
