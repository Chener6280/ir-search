from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime
from typing import Optional

from ir_search.adapters.base import AdapterError
from ir_search.config import load_yaml
from ir_search.models import Hit, Intent, Query
from ir_search.network import bool_env, http_error_message, open_url


class BochaAdapter:
    name = "bocha"
    mode = "live"
    endpoint = "https://api.bochaai.com/v1/web-search"

    def query(self, q: Query) -> list[Hit]:
        key = os.environ.get("BOCHA_API_KEY")
        if not key:
            raise AdapterError("BOCHA_API_KEY is not set")
        payload = {
            "query": build_bocha_query(q),
            "freshness": q.window.raw,
            "summary": True,
            "count": q.count,
        }
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with open_url(req, timeout=15, **bocha_network_options()) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise AdapterError(http_error_message("bocha request failed", exc), retryable=True) from exc
        except Exception as exc:
            raise AdapterError(f"bocha request failed: {exc}", retryable=True) from exc
        items = data.get("data", {}).get("webPages", {}).get("value", [])
        return [
            Hit(
                title=item.get("name") or "",
                url=item.get("url") or "",
                snippet=item.get("summary") or item.get("snippet") or "",
                source=self.name,
                published_at=_parse_dt(item.get("datePublished") or item.get("dateLastCrawled")),
            )
            for item in items
            if item.get("url")
        ]


def build_bocha_query(q: Query) -> str:
    if "site:" in q.text.lower():
        return q.text

    domains = focus_site_domains(q.intent)
    if not domains:
        return q.text
    site_clause = " OR ".join(f"site:{domain}" for domain in domains)
    return f"{q.text} ({site_clause})"


def bocha_network_options() -> dict:
    proxy_url = os.environ.get("BOCHA_PROXY")
    return {
        "proxy_url": proxy_url,
        "disable_proxy": False if proxy_url else bool_env("BOCHA_DISABLE_SYSTEM_PROXY", True),
    }


def focus_site_domains(intent: Intent = Intent.COMPANY_NEWS) -> list[str]:
    configured = load_yaml("source_focus_sites.yaml").get("bocha", {})
    return _dedupe_domains(configured.get(intent.name, []))


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


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
