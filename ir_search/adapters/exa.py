from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime
from typing import Optional

from ir_search.adapters.base import AdapterError
from ir_search.infrastructure.web_search import _legacy_search_failure
from ir_search.config import load_yaml
from ir_search.models import Hit, Intent, Query
from ir_search.network import bool_env, open_url


class ExaAdapter:
    name = "exa"
    mode = "live"
    endpoint = "https://api.exa.ai/search"

    def query(self, q: Query) -> list[Hit]:
        key = os.environ.get("EXA_API_KEY")
        if not key:
            raise AdapterError("EXA_API_KEY is not set")
        payload = build_exa_payload(q)
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"x-api-key": key, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with open_url(req, timeout=15, **exa_network_options()) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise AdapterError(_legacy_search_failure(exc), retryable=True) from None
        return [
            Hit(
                title=item.get("title") or "",
                url=item.get("url") or "",
                snippet=item.get("summary") or (item.get("text") or "")[:1000],
                source=self.name,
                published_at=_parse_dt(item.get("publishedDate")),
                raw_score=_clamp(item.get("score")),
            )
            for item in data.get("results", [])
            if item.get("url")
        ]


def build_exa_payload(q: Query) -> dict:
    search_type = os.environ.get('EXA_SEARCH_TYPE', 'auto')
    if search_type not in {'auto', 'lite'}: raise AdapterError('unsupported_exa_search_type')
    payload = {
        "query": q.text,
        "type": search_type,
        "numResults": q.count,
        "contents": {"text": {"maxCharacters": 1000}, "summary": True},
    }
    domains = focus_site_domains(intent=q.intent)
    if domains:
        payload["includeDomains"] = domains
    return payload


def exa_network_options() -> dict:
    return {
        "proxy_url": os.environ.get("EXA_PROXY"),
        "disable_proxy": bool_env("EXA_DISABLE_SYSTEM_PROXY", False),
    }


def focus_site_domains(category: Optional[str] = None, intent: Optional[Intent] = None) -> list[str]:
    if intent is not None:
        configured = load_yaml("source_focus_sites.yaml").get("exa", {})
        domains = configured.get(intent.name, [])
        if domains:
            return _dedupe_domains(domains)
        if intent == Intent.GENERAL:
            return []

    rows = load_yaml("exa_focus_sites.yaml").get("focus_sites", [])
    domains: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if category and row.get("category") != category:
            continue
        domain = (row.get("domain") or "").strip().lower()
        if domain and domain not in seen:
            seen.add(domain)
            domains.append(domain)
    return domains


def sentiment_site_domains(region: str) -> list[str]:
    rows = load_yaml("sentiment_sites.yaml").get(region, [])
    return _domains_from_rows(rows)


def _domains_from_rows(rows: list[dict]) -> list[str]:
    return _dedupe_domains([(row.get("domain") or "").strip().lower() for row in rows])


def _dedupe_domains(items: list[str]) -> list[str]:
    domains: list[str] = []
    seen: set[str] = set()
    for item in items:
        domain = item.strip().lower()
        if domain and domain not in seen:
            seen.add(domain)
            domains.append(domain)
    return domains


def _clamp(value: object) -> Optional[float]:
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
