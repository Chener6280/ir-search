"""Bounded official web discovery, with provider-isolated credentials and safe fields."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
import re

from ir_search.infrastructure.credentials import WebMaterialProfile
from ir_search.infrastructure.public_web import _request
from ir_search.registry import DataAdapterError

_ENDPOINT = "https://api.anysearch.com/v1/search"
_MAX_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class WebSearchPage:
    rows: list[dict] = field(repr=False)
    fetched_at: datetime


def _constant(_):
    raise ValueError()


def _failure_code(status, value):
    # Inspect only declared API error fields, never result/snippet text. Do not
    # expose raw messages, balances, keys or URLs in the normalized error.
    value = value if isinstance(value, dict) else {}
    code = value.get('code') if type(value.get('code')) is int else status
    message = ' '.join(v[:1000] for key in ('message', 'msg', 'error', 'detail', 'code')
                       if isinstance((v := value.get(key)), str)).lower()
    if status == 401 or code == 401:
        return 'authentication_failed'
    if (status == 429 or code == 429) and re.search(r'rate limit|per[ _-]*(?:second|minute)|req/min', message):
        return 'rate_limit'
    exhausted = re.search(r'quota[ _-]*(?:exhausted|exceeded)|insufficient[ _-]*(?:quota|credits?|balance)'
                          r'|no[ _-]*more[ _-]*credits|not enough credits|credits?[ _-]*exhausted'
                          r'|余额不足|额度(?:已)?(?:用尽|耗尽|不足)|配额(?:已)?(?:用尽|耗尽|不足)', message)
    if status in {402, 432, 433} or code in {402, 432, 433} or exhausted:
        return 'quota'
    if status == 429 or code == 429 or 'rate limit' in message:
        return 'rate_limit'
    if status == 403 or code == 403:
        return 'entitlement_denied'
    if 'unauthorized' in message or 'invalid api key' in message or 'invalid key' in message:
        return 'authentication_failed'
    return 'network' if status >= 500 or code == 500 else 'upstream_schema'


def _search_value(reply):
    if 300 <= reply.status < 400:
        raise DataAdapterError('blocked_url')
    is_json = reply.content_type.split(';', 1)[0].strip().lower() == 'application/json'
    try:
        value = json.loads(reply.body, parse_constant=_constant) if is_json else None
    except (ValueError, UnicodeError, RecursionError):
        value = None
    if reply.status != 200:
        raise DataAdapterError(_failure_code(reply.status, value))
    if not isinstance(value, dict):
        raise DataAdapterError('upstream_schema')
    return value


class ExaWebClient:
    """Independent deterministic search client, without legacy generated summaries."""
    def __init__(self, profile: WebMaterialProfile, *, transport=None):
        if not isinstance(profile, WebMaterialProfile):
            raise ValueError('WebMaterialProfile required')
        self._profile, self._transport = profile, transport or _request

    def search(self, query: str, *, limit: int, context, allowed_domains=()) -> WebSearchPage:
        """Return bounded discovery text, never promoted to verified original content."""
        if not isinstance(query, str) or not query.strip() or len(query) > 4000 or type(limit) is not int or not 1 <= limit <= 10:
            raise DataAdapterError('unsupported')
        if (not isinstance(allowed_domains, tuple) or len(allowed_domains) > 20
                or any(not isinstance(d, str) or len(d) > 253 or '..' in d
                       or not re.fullmatch(r'[a-z0-9]+(?:[.-][a-z0-9]+)*\.[a-z]{2,}', d) for d in allowed_domains)):
            raise DataAdapterError('unsupported')
        context.check_active()
        key = self._profile.exa_api_key
        if not key:
            raise DataAdapterError('no_credential')
        payload = {'query': query, 'type': 'auto', 'numResults': limit,
                   'contents': {'text': {'maxCharacters': 1000}}}
        if allowed_domains:
            payload['includeDomains'] = list(allowed_domains)
        reply = self._transport('https://api.exa.ai/search', method='POST', body=json.dumps(payload).encode(),
            headers={'Content-Type': 'application/json', 'Accept': 'application/json', 'x-api-key': key},
            max_bytes=_MAX_BYTES, context=context, search_api_errors=True)
        context.check_active()
        value = _search_value(reply)
        if value.get('error'):
            raise DataAdapterError(_failure_code(200, value))
        rows = value.get('results')
        if not isinstance(rows, list) or len(rows) > 100 or any(not isinstance(row, dict) for row in rows):
            raise DataAdapterError('upstream_schema')
        rows = [{dest: row[src] for src, dest in (('title', 'title'), ('url', 'url'),
                 ('text', 'snippet'), ('publishedDate', 'published_at')) if src in row} for row in rows]
        if key in json.dumps(rows, ensure_ascii=False):
            raise DataAdapterError('upstream_schema')
        return WebSearchPage(rows, reply.fetched_at)


class AnySearchWebClient:
    def __init__(self, profile: WebMaterialProfile, *, transport=None):
        if not isinstance(profile, WebMaterialProfile):
            raise ValueError("WebMaterialProfile required")
        self._profile, self._transport = profile, transport or _request

    def search(self, query: str, *, limit: int, context) -> WebSearchPage:
        """Call only general web search; reject raw errors and discard extra fields."""
        if not isinstance(query, str) or not query.strip() or len(query) > 4000 or type(limit) is not int or not 1 <= limit <= 10:
            raise DataAdapterError("unsupported")
        context.check_active()
        if not self._profile.api_key and not self._profile.allow_anonymous:
            raise DataAdapterError("no_credential")
        headers = {"Content-Type": "application/json", "Accept": "application/json", "X-Anysearch-Client": "ir-search/0.1"}
        if self._profile.api_key:
            headers["Authorization"] = "Bearer " + self._profile.api_key
        reply = self._transport(_ENDPOINT, method="POST", body=json.dumps({"query": query, "max_results": limit}).encode(),
                                headers=headers, max_bytes=_MAX_BYTES, context=context, search_api_errors=True)
        context.check_active()
        value = _search_value(reply)
        if not isinstance(value, dict) or type(value.get("code")) is not int:
            raise DataAdapterError("upstream_schema")
        if value["code"] != 0:
            raise DataAdapterError(_failure_code(200, value))
        data = value.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("results"), list) or len(data["results"]) > 100:
            raise DataAdapterError("upstream_schema")
        rows = data["results"]
        if any(not isinstance(row, dict) for row in rows):
            raise DataAdapterError("upstream_schema")
        # Preserve only declared discovery fields. Do not save auto-issued keys,
        # generated answers, account details, tool instructions or arbitrary URLs.
        rows = [{key: row[key] for key in ("title", "url", "snippet", "published_at") if key in row} for row in rows]
        if self._profile.api_key and self._profile.api_key in json.dumps(rows, ensure_ascii=False):
            raise DataAdapterError("upstream_schema")
        return WebSearchPage(rows, reply.fetched_at)


class BochaWebClient:
    def __init__(self, profile: WebMaterialProfile, *, transport=None):
        if not isinstance(profile, WebMaterialProfile):
            raise ValueError("WebMaterialProfile required")
        self._profile, self._transport = profile, transport or _request

    def search(self, query: str, *, limit: int, context, allowed_domains=()) -> WebSearchPage:
        """Read bounded Bocha web results; never use crawl dates as publication dates."""
        if not isinstance(query, str) or not query.strip() or len(query) > 4000 or type(limit) is not int or not 1 <= limit <= 10:
            raise DataAdapterError("unsupported")
        if (not isinstance(allowed_domains, tuple) or len(allowed_domains) > 20
                or any(not isinstance(d, str) or len(d) > 253 or not re.fullmatch(r'[a-z0-9]+(?:[.-][a-z0-9]+)*\.[a-z]{2,}', d)
                       for d in allowed_domains)):
            raise DataAdapterError('unsupported')
        context.check_active()
        key = self._profile.bocha_api_key
        if not key: raise DataAdapterError("no_credential")
        payload = {"query": query, "count": limit, "freshness": "noLimit", "summary": False}
        if allowed_domains:
            payload['include'] = '|'.join(allowed_domains)
        reply = self._transport("https://api.bochaai.com/v1/web-search", method="POST",
            body=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json", "Authorization": "Bearer " + key},
            max_bytes=_MAX_BYTES, context=context, search_api_errors=True)
        context.check_active()
        value = _search_value(reply)
        if not isinstance(value, dict) or type(value.get("code")) is not int:
            raise DataAdapterError("upstream_schema")
        if value["code"] != 200:
            raise DataAdapterError(_failure_code(200, value))
        data = value.get("data")
        pages = data.get("webPages") if isinstance(data, dict) else None
        rows = pages.get("value") if isinstance(pages, dict) else None
        if not isinstance(rows, list) or len(rows) > 100 or any(not isinstance(row, dict) for row in rows):
            raise DataAdapterError("upstream_schema")
        # summary/content may be transformed text. Only ordinary discovery snippets
        # are retained, never promoted to source text without an independent read.
        rows = [{dest: row[src] for src, dest in (("name", "title"), ("url", "url"),
                 ("snippet", "snippet"), ("datePublished", "published_at")) if src in row} for row in rows]
        if key in json.dumps(rows, ensure_ascii=False): raise DataAdapterError("upstream_schema")
        return WebSearchPage(rows, reply.fetched_at)
