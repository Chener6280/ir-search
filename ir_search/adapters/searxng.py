from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ir_search.adapters.base import AdapterError
from ir_search.models import EvidenceType, Hit, Lang, Query
from ir_search.network import bool_env, http_error_message, open_url


DEFAULT_SEARXNG_URL = "http://localhost:8080"


class SearXNGAdapter:
    name = "searxng"
    mode = "fallback"

    def query(self, q: Query) -> list[Hit]:
        if not searxng_enabled():
            raise AdapterError("SEARXNG_ENABLED is not true", retryable=False)

        endpoint = os.environ.get("SEARXNG_URL", DEFAULT_SEARXNG_URL)
        timeout = _int_env("SEARXNG_TIMEOUT", 10)
        max_results = min(q.count, _int_env("SEARXNG_MAX_RESULTS", 10))
        engines = _split_env("SEARXNG_ENGINES")
        url = build_searxng_url(endpoint, q, engines)
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": os.environ.get("SEARXNG_USER_AGENT", "ir-search/0.1"),
            },
            method="GET",
        )

        try:
            with open_url(req, timeout=timeout, **searxng_network_options()) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            message = http_error_message("searxng request failed", exc)
            _log_failure(q, endpoint, _http_error_type(exc), message)
            raise AdapterError(message, retryable=True) from exc
        except json.JSONDecodeError as exc:
            message = f"searxng request failed: invalid JSON: {exc}"
            _log_failure(q, endpoint, "invalid_json", message)
            raise AdapterError(message, retryable=True) from exc
        except Exception as exc:
            error_type = _exception_error_type(exc)
            message = f"searxng request failed: {exc}"
            _log_failure(q, endpoint, error_type, message)
            raise AdapterError(message, retryable=True) from exc

        items = data.get("results", [])
        if not isinstance(items, list):
            message = "searxng request failed: JSON response missing list field 'results'"
            _log_failure(q, endpoint, "invalid_json", message)
            raise AdapterError(message, retryable=True)

        fetched_at = datetime.now(timezone.utc)
        hits: list[Hit] = []
        for rank, item in enumerate(items[:max_results], start=1):
            if not isinstance(item, dict) or not item.get("url"):
                continue
            engine = _engine_name(item)
            hits.append(
                Hit(
                    title=item.get("title") or "",
                    url=item.get("url") or "",
                    snippet=item.get("content") or item.get("snippet") or "",
                    source=self.name,
                    evidence_type=EvidenceType.UNKNOWN,
                    published_at=_parse_dt(item.get("publishedDate") or item.get("date")),
                    fetched_at=fetched_at,
                    raw_score=_score(item.get("score")),
                    extra={
                        "provider": "searxng",
                        "adapter_mode": self.mode,
                        "query": q.text,
                        "engine": engine,
                        "rank": rank,
                        "result_kind": "discovery_url",
                        "coverage_status": "partial_discovery",
                        "evidence_type": "search_result",
                        "confidence": "low_to_medium",
                        "promotable": True,
                        "promotion_required": True,
                    },
                )
            )
        return hits


def searxng_enabled() -> bool:
    return bool_env("SEARXNG_ENABLED", False)


def build_searxng_url(base_url: str, q: Query, engines: Optional[list[str]] = None) -> str:
    base = base_url.rstrip("/")
    endpoint = base if base.endswith("/search") else f"{base}/search"
    params = {
        "q": q.text,
        "format": "json",
    }
    language = _language(q.lang)
    if language:
        params["language"] = language
    if engines:
        params["engines"] = ",".join(engines)
    return f"{endpoint}?{urllib.parse.urlencode(params)}"


def searxng_network_options() -> dict:
    proxy_url = os.environ.get("SEARXNG_PROXY")
    return {
        "proxy_url": proxy_url,
        "disable_proxy": False if proxy_url else bool_env("SEARXNG_DISABLE_SYSTEM_PROXY", True),
    }


def _split_env(name: str) -> list[str]:
    value = os.environ.get(name, "")
    return [item.strip() for item in value.split(",") if item.strip()]


def _int_env(name: str, default: int) -> int:
    value = os.environ.get(name)
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(0, parsed)


def _language(lang: Lang) -> Optional[str]:
    if lang == Lang.ZH:
        return "zh-CN"
    if lang == Lang.EN:
        return "en"
    if lang == Lang.MIXED:
        return "all"
    return None


def _engine_name(item: dict) -> Optional[str]:
    engine = item.get("engine")
    if engine:
        return str(engine)
    engines = item.get("engines")
    if isinstance(engines, list) and engines:
        return str(engines[0])
    return None


def _score(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return None


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _http_error_type(exc: urllib.error.HTTPError) -> str:
    if exc.code == 403:
        return "http_403"
    if exc.code == 429:
        return "http_429"
    return f"http_{exc.code}"


def _exception_error_type(exc: Exception) -> str:
    text = str(exc).lower()
    if isinstance(exc, TimeoutError) or "timed out" in text or "timeout" in text:
        return "timeout"
    if isinstance(exc, urllib.error.URLError):
        return "connection_error"
    return "request_error"


def _log_failure(q: Query, searxng_url: str, error_type: str, message: str) -> None:
    path = Path(os.environ.get("SEARXNG_FAILURE_LOG", "logs/searxng_failures.log"))
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "query": q.text,
        "searxng_url": searxng_url,
        "error_type": error_type,
        "error_message": message,
        "retry_count": 0,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
