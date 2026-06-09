from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime
from typing import Optional

from ir_search.adapters.base import AdapterError
from ir_search.models import Hit, Lang, Query
from ir_search.network import http_error_message, open_url


class AnySearchAdapter:
    endpoint = "https://api.anysearch.com/v1/search"
    mode = "experimental"

    def __init__(self, name: str = "anysearch", auth_mode: str = "required") -> None:
        self.name = name
        self.auth_mode = auth_mode

    def query(self, q: Query) -> list[Hit]:
        key = os.environ.get("ANYSEARCH_API_KEY")
        if self.auth_mode == "required" and not key:
            raise AdapterError("ANYSEARCH_API_KEY is not set", retryable=True)

        headers = {"Content-Type": "application/json"}
        if self.auth_mode != "anonymous" and key:
            headers["Authorization"] = f"Bearer {key}"

        req = urllib.request.Request(
            os.environ.get("ANYSEARCH_ENDPOINT", self.endpoint),
            data=json.dumps(build_anysearch_payload(q)).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with open_url(req, timeout=int(os.environ.get("ANYSEARCH_TIMEOUT", "20"))) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise AdapterError(http_error_message(f"{self.name} request failed", exc), retryable=True) from exc
        except Exception as exc:
            raise AdapterError(f"{self.name} request failed: {exc}", retryable=True) from exc

        items = data.get("results") or data.get("data", {}).get("results", [])
        metadata = data.get("metadata") or data.get("data", {}).get("metadata", {})
        return [
            Hit(
                title=item.get("title") or "",
                url=item.get("url") or "",
                snippet=item.get("description") or item.get("content") or item.get("raw_content") or "",
                source=self.name,
                published_at=_parse_dt(item.get("published_at")),
                raw_score=_score(item),
                extra={
                    "experimental": True,
                    "schema_verified": False,
                    "provider_source": item.get("source"),
                    "quality_score": item.get("quality_score"),
                    "signal_scores": item.get("signal_scores"),
                    "metadata": metadata,
                },
            )
            for item in items
            if item.get("url")
        ]


def build_anysearch_payload(q: Query) -> dict:
    payload = {
        "query": q.text,
        "max_results": q.count,
        "domains": ["finance", "business"],
        "content_types": ["web", "news"],
        "zone": _zone(q.lang),
        "language": _language(q.lang),
    }
    freshness = _freshness(q.window.raw)
    if freshness:
        payload["constraint"] = {"freshness": freshness}
    return payload


def _zone(lang: Lang) -> str:
    return "cn" if lang == Lang.ZH else "intl"


def _language(lang: Lang) -> str:
    if lang == Lang.ZH:
        return "zh-CN"
    if lang == Lang.EN:
        return "en"
    return "zh-CN" if lang == Lang.MIXED else "en"


def _freshness(raw: str) -> Optional[str]:
    return {
        "oneDay": "day",
        "oneWeek": "week",
        "oneMonth": "month",
        "oneYear": "year",
    }.get(raw)


def _score(item: dict) -> Optional[float]:
    value = item.get("quality_score", item.get("score"))
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
