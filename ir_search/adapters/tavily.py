from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime
from typing import Optional

from ir_search.adapters.base import AdapterError
from ir_search.models import Hit, Query
from ir_search.network import http_error_message, open_url


class TavilyAdapter:
    name = "tavily"
    mode = "live"
    endpoint = "https://api.tavily.com/search"

    def query(self, q: Query) -> list[Hit]:
        key = os.environ.get("TAVILY_API_KEY")
        if not key:
            raise AdapterError("TAVILY_API_KEY is not set", retryable=True)

        req = urllib.request.Request(
            os.environ.get("TAVILY_ENDPOINT", self.endpoint),
            data=json.dumps(build_tavily_payload(q)).encode("utf-8"),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with open_url(req, timeout=int(os.environ.get("TAVILY_TIMEOUT", "20"))) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise AdapterError(http_error_message("tavily request failed", exc), retryable=True) from exc
        except Exception as exc:
            raise AdapterError(f"tavily request failed: {exc}", retryable=True) from exc

        return [
            Hit(
                title=item.get("title") or "",
                url=item.get("url") or "",
                snippet=item.get("content") or item.get("raw_content") or "",
                source=self.name,
                published_at=_parse_dt(item.get("published_date")),
                raw_score=_score(item.get("score")),
                extra={"provider": "tavily"},
            )
            for item in data.get("results", [])
            if item.get("url")
        ]


def build_tavily_payload(q: Query) -> dict:
    return {
        "query": q.text,
        "max_results": q.count,
        "search_depth": os.environ.get("TAVILY_SEARCH_DEPTH", "basic"),
        "topic": os.environ.get("TAVILY_TOPIC", "finance"),
        "include_answer": False,
        "include_raw_content": False,
    }


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
