from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ir_search.adapters.base import AdapterError
from ir_search.models import EvidenceType, Hit, Query, SourceTier


class ZsxqAdapter:
    name = "zsxq"
    mode = "live"

    def query(self, q: Query) -> list[Hit]:
        command = zsxq_command()
        group_ids = zsxq_group_ids()
        if not group_ids:
            raise AdapterError("ZSXQ_GROUP_IDS is not set; configure comma-separated group ids", retryable=False)

        rows: list[dict[str, Any]] = []
        errors: list[str] = []
        for group_id in group_ids:
            try:
                completed = subprocess.run(
                    command + ["topic", "+search", "--group-id", group_id, "--query", q.text, "--json"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=int(os.environ.get("ZSXQ_CLI_TIMEOUT", "60")),
                )
            except FileNotFoundError as exc:
                raise AdapterError(f"zsxq-cli command not found: {exc}", retryable=False) from exc
            except subprocess.TimeoutExpired as exc:
                raise AdapterError(f"zsxq-cli timed out: {exc}", retryable=True) from exc
            except subprocess.CalledProcessError as exc:
                errors.append(_cli_error(group_id, exc))
                continue

            try:
                rows.extend(_extract_rows(json.loads(completed.stdout), group_id))
            except json.JSONDecodeError as exc:
                errors.append(f"group {group_id}: zsxq-cli stdout is not JSON: {exc}")

        hits = rows_to_hits(rows)
        if hits:
            return hits
        if errors:
            raise AdapterError("; ".join(errors), retryable=_errors_retryable(errors))
        return []


def zsxq_command() -> list[str]:
    configured = os.environ.get("ZSXQ_CLI_COMMAND")
    if configured:
        return shlex.split(configured)

    found = shutil.which("zsxq-cli")
    if found:
        return [found]

    candidates = [
        Path.home() / ".hermes/node/bin/zsxq-cli",
        Path.home() / ".npm-global/bin/zsxq-cli",
        Path("/opt/homebrew/bin/zsxq-cli"),
        Path("/usr/local/bin/zsxq-cli"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return [str(candidate)]
    return ["zsxq-cli"]


def zsxq_group_ids() -> list[str]:
    raw = os.environ.get("ZSXQ_GROUP_IDS", "")
    return [item.strip() for item in raw.replace(";", ",").split(",") if item.strip()]


def rows_to_hits(rows: list[dict[str, Any]]) -> list[Hit]:
    hits: list[Hit] = []
    for row in rows:
        title = _first_text(row, ["title", "subject", "name"]) or _title_from_body(row)
        topic_id = _first_text(row, ["topic_id", "topicId", "id"])
        url = _first_text(row, ["url", "link", "href"]) or _topic_url(topic_id)
        if not title or not url:
            continue
        snippet = _first_text(row, ["snippet", "summary", "text", "content", "description"]) or ""
        group_id = _first_text(row, ["group_id", "groupId"]) or ""
        group_name = _first_text(row, ["group_name", "groupName"]) or ""
        hits.append(
            Hit(
                title=title,
                url=url,
                snippet=snippet,
                source=ZsxqAdapter.name,
                tier=SourceTier.UGC,
                evidence_type=EvidenceType.SOCIAL_POST,
                published_at=parse_time(_first_text(row, ["created_at", "create_time", "createTime", "published_at", "time"])),
                extra={
                    "platform": "zsxq",
                    "group_id": group_id,
                    "group_name": group_name,
                    "topic_id": topic_id,
                    "requires_login": True,
                    "extraction_method": "zsxq_cli",
                },
            )
        )
    return hits


def parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if text.isdigit():
        ts = float(text)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, timezone.utc)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _extract_rows(data: Any, group_id: str) -> list[dict[str, Any]]:
    rows = _find_row_list(data)
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            copied = dict(row)
            copied.setdefault("group_id", group_id)
            out.append(copied)
    return out


def _find_row_list(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ["topics", "items", "list", "data", "records", "results"]:
        value = data.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _find_row_list(value)
            if nested:
                return nested
    return []


def _first_text(row: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _title_from_body(row: dict[str, Any]) -> str:
    body = _first_text(row, ["text", "content", "description"])
    return body.splitlines()[0][:80].strip() if body else ""


def _topic_url(topic_id: str) -> str:
    return f"https://wx.zsxq.com/dweb2/index/topic_detail/{topic_id}" if topic_id else ""


def _cli_error(group_id: str, exc: subprocess.CalledProcessError) -> str:
    detail = (exc.stderr or exc.stdout or str(exc)).strip()
    if exc.returncode == 11:
        return f"group {group_id}: zsxq-cli not logged in or token expired: {detail}"
    if exc.returncode == 13:
        return f"group {group_id}: zsxq-cli permission denied: {detail}"
    return f"group {group_id}: zsxq-cli failed with exit {exc.returncode}: {detail}"


def _errors_retryable(errors: list[str]) -> bool:
    joined = " ".join(errors).lower()
    return "network" in joined or "timed out" in joined or "exit 1" in joined
