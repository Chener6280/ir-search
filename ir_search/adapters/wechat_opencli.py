from __future__ import annotations

import json
import os
import shlex
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ir_search.adapters.base import AdapterError
from ir_search.models import EvidenceType, Hit, Query, SourceTier


REQUIRED_FIELDS = ["title", "url"]
OPTIONAL_FIELDS = ["snippet", "published_at", "account_name"]


class WechatOpenCLIAdapter:
    name = "wechat_opencli"
    mode = "live"

    def query(self, q: Query) -> list[Hit]:
        command = os.environ.get("WECHAT_OPENCLI_COMMAND")
        if not command:
            raise AdapterError("WECHAT_OPENCLI_COMMAND is not set; browser adapter is unavailable")

        try:
            completed = subprocess.run(
                shlex.split(command) + [q.text],
                check=True,
                capture_output=True,
                text=True,
                timeout=int(os.environ.get("WECHAT_OPENCLI_TIMEOUT", "60")),
            )
        except FileNotFoundError as exc:
            raise AdapterError(f"wechat opencli command not found: {exc}", retryable=True) from exc
        except subprocess.TimeoutExpired as exc:
            raise AdapterError(f"wechat opencli timed out: {exc}", retryable=True) from exc
        except subprocess.CalledProcessError as exc:
            raise AdapterError(f"wechat opencli failed: {exc.stderr or exc}", retryable=True) from exc

        rows = _parse_stdout(completed.stdout)
        hits = rows_to_hits(rows)
        if not hits:
            raise AdapterError("wechat opencli returned no valid rows", retryable=True)
        return hits


def _parse_stdout(stdout: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise AdapterError(f"wechat opencli stdout is not JSON: {exc}", retryable=True) from exc
    if not isinstance(data, list):
        raise AdapterError("wechat opencli stdout must be a JSON list", retryable=True)
    return [row for row in data if isinstance(row, dict)]


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
            "extraction_method": "opencli_browser",
            "requires_login": True,
        }
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
    if not value:
        return None
    text = value.strip()
    now = datetime.now(timezone.utc)
    if text == "昨天":
        return now - timedelta(days=1)
    if text == "前天":
        return now - timedelta(days=2)
    if text.endswith("小时前"):
        try:
            return now - timedelta(hours=int(text[:-3]))
        except ValueError:
            return None

    for fmt in ["%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d"]:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None
