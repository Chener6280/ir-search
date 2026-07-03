from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ir_search.adapters.base import AdapterError
from ir_search.models import EvidenceType, Hit, Query, SourceTier


READ_ONLY_COMMANDS = {
    "quote",
    "news",
    "institution-rating",
}

FORBIDDEN_COMMANDS = {
    "account",
    "assets",
    "bank-cards",
    "cash-flow",
    "dca",
    "deposits",
    "fund-positions",
    "max-qty",
    "order",
    "orders",
    "portfolio",
    "positions",
    "profit-analysis",
    "statement",
    "trade",
    "withdrawals",
}

EXPLICIT_SYMBOL_RE = re.compile(r"\b[A-Z0-9]{1,10}\.(?:US|HK|SH|SZ|SG)\b", re.IGNORECASE)
A_SHARE_RE = re.compile(r"(?<!\d)(?:6\d{5}|[03]\d{5})(?!\d)")


class LongbridgeAdapter:
    """Read-only Longbridge adapter.

    This adapter intentionally exposes only quote/news/institution-rating.
    Portfolio, account, order, and trading commands are blocked in the
    command guard before subprocess execution.
    """

    name = "longbridge"
    mode = "live"

    def query(self, q: Query) -> list[Hit]:
        command = longbridge_command()
        hits: list[Hit] = []
        errors: list[str] = []

        try:
            data = run_longbridge(command, ["news", "search", q.text, "--count", str(max(q.count, 10)), "--format", "json"])
            hits.extend(news_rows_to_hits(extract_rows(data), q.text))
        except AdapterError as exc:
            errors.append(str(exc))

        for symbol in symbols_from_query(q.text)[:5]:
            try:
                data = run_longbridge(command, ["quote", symbol, "--format", "json"])
                hits.extend(quote_rows_to_hits(extract_rows(data), symbol))
            except AdapterError as exc:
                errors.append(str(exc))

            try:
                data = run_longbridge(command, ["institution-rating", symbol, "--format", "json"])
                hits.extend(rating_to_hits(data, symbol))
            except AdapterError as exc:
                errors.append(str(exc))

        if hits:
            return hits[: max(q.count, 10)]
        if errors:
            raise AdapterError("; ".join(errors), retryable=any(is_retryable_error(error) for error in errors))
        return []


def longbridge_command() -> list[str]:
    configured = os.environ.get("LONGBRIDGE_CLI_COMMAND")
    if configured:
        return shlex.split(configured)

    found = shutil.which("longbridge")
    if found:
        return [found]

    candidates = [
        Path("/opt/homebrew/bin/longbridge"),
        Path("/usr/local/bin/longbridge"),
        Path.home() / ".cargo/bin/longbridge",
        Path.home() / ".hermes/bin/longbridge",
    ]
    for candidate in candidates:
        if candidate.exists():
            return [str(candidate)]
    return ["longbridge"]


def run_longbridge(command: list[str], args: list[str]) -> Any:
    ensure_read_only_args(args)
    try:
        completed = subprocess.run(
            command + args,
            check=True,
            capture_output=True,
            text=True,
            timeout=int(os.environ.get("LONGBRIDGE_CLI_TIMEOUT", "45")),
        )
    except FileNotFoundError as exc:
        raise AdapterError(f"longbridge CLI command not found: {exc}", retryable=False) from exc
    except subprocess.TimeoutExpired as exc:
        raise AdapterError(f"longbridge CLI timed out: {exc}", retryable=True) from exc
    except subprocess.CalledProcessError as exc:
        raise AdapterError(_cli_error(exc), retryable=is_retryable_error(exc.stderr or exc.stdout or str(exc))) from exc

    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AdapterError(f"longbridge CLI stdout is not JSON: {exc}", retryable=False) from exc


def ensure_read_only_args(args: list[str]) -> None:
    if not args:
        raise AdapterError("longbridge command is empty", retryable=False)
    command = args[0].lower()
    if command not in READ_ONLY_COMMANDS:
        raise AdapterError(f"longbridge command '{command}' is not enabled for this read-only adapter", retryable=False)
    forbidden = [part for part in args if part.lower() in FORBIDDEN_COMMANDS]
    if forbidden:
        raise AdapterError(f"longbridge command blocked by read-only guard: {', '.join(forbidden)}", retryable=False)


def symbols_from_query(text: str) -> list[str]:
    symbols = [match.group(0).upper() for match in EXPLICIT_SYMBOL_RE.finditer(text)]
    for match in A_SHARE_RE.finditer(text):
        code = match.group(0)
        suffix = ".SH" if code.startswith("6") else ".SZ"
        symbols.append(f"{code}{suffix}")
    return list(dict.fromkeys(symbols))


def news_rows_to_hits(rows: list[dict[str, Any]], query_text: str) -> list[Hit]:
    hits: list[Hit] = []
    for row in rows:
        title = first_text(row, ["title", "headline", "name"])
        if not title:
            continue
        article_id = first_text(row, ["id", "news_id", "article_id"])
        url = first_text(row, ["url", "link", "href"]) or longbridge_article_url(article_id)
        if not url:
            continue
        snippet = first_text(row, ["summary", "snippet", "description", "content", "text"])
        hits.append(
            Hit(
                title=title,
                url=url,
                snippet=snippet,
                source=LongbridgeAdapter.name,
                tier=SourceTier.MEDIA,
                evidence_type=EvidenceType.NEWS,
                published_at=parse_time(first_text(row, ["published_at", "created_at", "time", "date"])),
                extra={
                    "platform": "longbridge",
                    "kind": "news",
                    "article_id": article_id,
                    "query": query_text,
                    "requires_login": True,
                    "read_only": True,
                    "disabled_capabilities": sorted(FORBIDDEN_COMMANDS),
                    "extraction_method": "longbridge_cli",
                },
            )
        )
    return hits


def quote_rows_to_hits(rows: list[dict[str, Any]], symbol: str) -> list[Hit]:
    hits: list[Hit] = []
    for row in rows:
        row_symbol = first_text(row, ["symbol", "code", "ticker"]) or symbol
        last = first_text(row, ["last_done", "last", "price", "last_price"])
        change = first_text(row, ["change_rate", "change_percent", "change"])
        volume = first_text(row, ["volume"])
        turnover = first_text(row, ["turnover"])
        parts = [
            f"last={last}" if last else "",
            f"change={change}" if change else "",
            f"volume={volume}" if volume else "",
            f"turnover={turnover}" if turnover else "",
        ]
        hits.append(
            Hit(
                title=f"Longbridge quote: {row_symbol}",
                url=security_url(row_symbol),
                snippet="; ".join(part for part in parts if part),
                source=LongbridgeAdapter.name,
                tier=SourceTier.MEDIA,
                evidence_type=EvidenceType.DATA_TABLE,
                extra={
                    "platform": "longbridge",
                    "kind": "quote",
                    "symbol": row_symbol,
                    "raw": row,
                    "requires_login": True,
                    "read_only": True,
                    "extraction_method": "longbridge_cli",
                },
            )
        )
    return hits


def rating_to_hits(data: Any, symbol: str) -> list[Hit]:
    analyst = data.get("analyst", data) if isinstance(data, dict) else {}
    if not isinstance(analyst, dict):
        return []
    evaluate = analyst.get("evaluate", {})
    target = analyst.get("target", {})
    if not isinstance(evaluate, dict) and not isinstance(target, dict):
        return []
    if not evaluate and not target:
        return []
    snippet_parts = []
    if isinstance(evaluate, dict):
        snippet_parts.append(", ".join(f"{key}={value}" for key, value in evaluate.items()))
    if isinstance(target, dict):
        snippet_parts.append(", ".join(f"{key}={value}" for key, value in target.items()))
    return [
        Hit(
            title=f"Longbridge institution rating: {symbol}",
            url=security_url(symbol),
            snippet="; ".join(part for part in snippet_parts if part),
            source=LongbridgeAdapter.name,
            tier=SourceTier.MEDIA,
            evidence_type=EvidenceType.DATA_TABLE,
            extra={
                "platform": "longbridge",
                "kind": "institution_rating",
                "symbol": symbol,
                "raw": data,
                "requires_login": True,
                "read_only": True,
                "extraction_method": "longbridge_cli",
            },
        )
    ]


def extract_rows(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if not isinstance(data, dict):
        return []
    for key in ["items", "list", "data", "records", "results", "news", "quotes"]:
        value = data.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            nested = extract_rows(value)
            if nested:
                return nested
    if any(key in data for key in ["symbol", "title", "last", "last_done", "price"]):
        return [data]
    return []


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


def first_text(row: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def longbridge_article_url(article_id: str) -> str:
    return f"https://longbridge.com/news/{article_id}" if article_id else ""


def security_url(symbol: str) -> str:
    return f"https://longbridge.com/quote/{symbol}" if symbol else "https://longbridge.com"


def is_retryable_error(error: str) -> bool:
    lower = error.lower()
    return any(needle in lower for needle in ["timeout", "timed out", "temporarily", "network", "connection", "429"])


def _cli_error(exc: subprocess.CalledProcessError) -> str:
    detail = (exc.stderr or exc.stdout or str(exc)).strip()
    return f"longbridge CLI failed with exit {exc.returncode}: {detail}"
