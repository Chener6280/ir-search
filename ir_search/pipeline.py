from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Mapping, Optional, Union

from .adapters.base import AdapterError, SearchAdapter
from .cache import CallLogger, FileCache, cache_key
from .config import load_yaml
from .entity import match_entities, normalize_entities
from .evidence import classify_hit
from .models import FallbackPolicy, Hit, Intent, Lang, Query, SearchResult, SourceStatus
from .rerank import rerank
from .urlnorm import canonicalize_url


def prepare_query(x: Union[str, Query]) -> Query:
    q = Query.of(x)
    q.lang = classify_language(q.text) if q.lang == Lang.AUTO else q.lang
    if q.intent == Intent.GENERAL:
        apply_intent_detection(q)
    elif not q.intent_scores:
        q.intent_scores = {q.intent.name: 1.0}
    return normalize_entities(q)


def classify_language(text: str) -> Lang:
    has_zh = any("\u4e00" <= ch <= "\u9fff" for ch in text)
    has_en = any(("a" <= ch.lower() <= "z") for ch in text)
    if has_zh and has_en:
        return Lang.MIXED
    if has_zh:
        return Lang.ZH
    if has_en:
        return Lang.EN
    return Lang.AUTO


def detect_intent(text: str) -> Intent:
    scores = score_intents(text)
    if scores:
        return max(scores, key=scores.get)
    if any(ch.isdigit() for ch in text):
        return Intent.COMPANY_NEWS
    return Intent.GENERAL


def apply_intent_detection(q: Query) -> Query:
    scores = score_intents(q.text)
    if not scores:
        q.intent = Intent.COMPANY_NEWS if any(ch.isdigit() for ch in q.text) else Intent.GENERAL
        q.intent_scores = {q.intent.name: 1.0 if q.intent != Intent.GENERAL else 0.0}
        q.secondary_intents = []
        return q

    q.intent = max(scores, key=scores.get)
    q.intent_scores = {intent.name: round(score, 4) for intent, score in sorted(scores.items(), key=lambda item: item[1], reverse=True)}
    q.secondary_intents = [intent for intent, score in scores.items() if intent != q.intent and score >= 0.3]
    return q


def score_intents(text: str) -> dict[Intent, float]:
    lower = text.lower()
    rules = intent_rules()
    scores: dict[Intent, float] = {}
    for intent, needles in rules.items():
        matched = [needle for needle in needles if needle in lower]
        if matched:
            scores[intent] = min(1.0, 0.45 + 0.18 * len(matched))
    if not scores and any(ch.isdigit() for ch in text):
        scores[Intent.COMPANY_NEWS] = 0.4
    return scores


def intent_rules() -> dict[Intent, list[str]]:
    raw_rules = load_yaml("intent_rules.yaml").get("rules", {})
    rules: dict[Intent, list[str]] = {}
    for intent_name, needles in raw_rules.items():
        if intent_name in Intent.__members__ and isinstance(needles, list):
            rules[Intent[intent_name]] = [str(needle).lower() for needle in needles]
    return rules


def select_sources(q: Query) -> list[str]:
    if q.sources is not None:
        return [_normalize_source_name(source) for source in q.sources]

    routes = load_yaml("source_routes.yaml").get("routes", {})
    intent_key = q.intent.name
    route = routes.get(intent_key, routes["GENERAL"])
    sources = route.get(q.lang.value) or route.get("default") or routes["GENERAL"].get(q.lang.value, [])

    if _mentions_wechat(q.text):
        sources = list(sources)
        if "manual_wechat" not in sources:
            sources.append("manual_wechat")
        if "wechat_opencli" not in sources:
            sources.append("wechat_opencli")
    if q.allow_browser_fallback and q.intent == Intent.BROKER_RESEARCH and "wechat_opencli" not in sources:
        sources = list(sources) + ["wechat_opencli"]
    return sources


def run_pipeline(
    x: Union[str, Query],
    registry: Mapping[str, SearchAdapter],
    cache: Optional[FileCache] = None,
    logger: Optional[CallLogger] = None,
) -> SearchResult:
    q = prepare_query(x)
    sources = select_sources(q)
    raw_hits, diagnostics = fan_out(q, sources, registry, cache, logger)
    hits = normalize_hits(q, raw_hits)
    hits = dedup_hits(hits)
    hits = rerank(q, hits)[: q.count]
    return SearchResult(query=q, hits=hits, diagnostics=diagnostics)


def fan_out(
    q: Query,
    sources: list[str],
    registry: Mapping[str, SearchAdapter],
    cache: Optional[FileCache],
    logger: Optional[CallLogger],
) -> tuple[list[Hit], list[SourceStatus]]:
    hits: list[Hit] = []
    diagnostics: list[SourceStatus] = []
    attempted: set[str] = set()
    for source in sources:
        source_hits, status = _query_source(q, source, registry, cache)
        attempted.add(source)
        hits.extend(source_hits)
        diagnostics.append(status)
        if logger:
            logger.write({"source": source, "query": q.text, "diagnostic": status.__dict__})

        if should_trigger_fallback(q, status):
            fallback_parent_source = source
            fallback_parent_status = status
            for fallback_source in fallback_sources_for(source):
                if fallback_source in attempted:
                    continue
                fallback_hits, fallback_status = _query_source(q, fallback_source, registry, cache)
                attempted.add(fallback_source)
                fallback_status.error = _with_fallback_reason(fallback_parent_source, fallback_parent_status, fallback_status)
                mark_fallback_hits(fallback_hits, fallback_parent_source)
                hits.extend(fallback_hits)
                diagnostics.append(fallback_status)
                if logger:
                    logger.write({"source": fallback_source, "query": q.text, "diagnostic": fallback_status.__dict__})
                if fallback_status.ok and fallback_hits:
                    break
                if should_trigger_fallback(q, fallback_status):
                    fallback_parent_source = fallback_source
                    fallback_parent_status = fallback_status
    return hits, diagnostics


def _query_source(
    q: Query,
    source: str,
    registry: Mapping[str, SearchAdapter],
    cache: Optional[FileCache],
) -> tuple[list[Hit], SourceStatus]:
    start = time.perf_counter()
    adapter = registry.get(source)
    if adapter is None:
        return [], SourceStatus(source=source, ok=False, n_results=0, error="unknown source", elapsed_ms=0, adapter_mode="unknown")

    key = cache_key(source, q)
    cached = cache.get(key) if cache else None
    if cached is not None:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        adapter_mode = getattr(adapter, "mode", "unknown")
        return cached, SourceStatus(source=source, ok=True, n_results=len(cached), error=None, elapsed_ms=elapsed_ms, cache_hit=True, adapter_mode=adapter_mode)

    errors: list[str] = []
    max_attempts = 2
    for attempt in range(1, max_attempts + 1):
        try:
            source_hits = adapter.query(q)
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            adapter_mode = getattr(adapter, "mode", "unknown")
            for hit in source_hits:
                hit.extra.setdefault("adapter_mode", adapter_mode)
            if cache:
                cache.set(key, source_hits)
            error = f"retry_after={errors[0]}" if errors else None
            return source_hits, SourceStatus(source=source, ok=True, n_results=len(source_hits), error=error, elapsed_ms=elapsed_ms, cache_hit=False, adapter_mode=adapter_mode)
        except AdapterError as exc:
            errors.append(str(exc))
            if not exc.retryable or attempt == max_attempts:
                elapsed_ms = int((time.perf_counter() - start) * 1000)
                adapter_mode = getattr(adapter, "mode", "unknown")
                error = str(exc)
                if exc.retryable and len(errors) > 1:
                    error = f"retryable failed after {len(errors)} attempts: {'; '.join(errors)}"
                return [], SourceStatus(source=source, ok=False, n_results=0, error=error, elapsed_ms=elapsed_ms, cache_hit=False, adapter_mode=adapter_mode)

    raise AssertionError("unreachable")


def fallback_sources_for(source: str) -> list[str]:
    return load_yaml("fallback_routes.yaml").get("fallbacks", {}).get(source, [])


def should_trigger_fallback(q: Query, status: SourceStatus) -> bool:
    if not q.allow_fallback:
        return False
    if q.fallback_policy == FallbackPolicy.NONE:
        return False
    if status.ok:
        return q.fallback_on_empty and status.n_results == 0 and q.fallback_policy == FallbackPolicy.ALL
    if not status.error:
        return False
    if q.fallback_policy == FallbackPolicy.ALL:
        return True
    error_type = classify_fallback_error(status.error)
    if q.fallback_policy == FallbackPolicy.QUOTA_ONLY:
        return error_type == "quota"
    if q.fallback_policy == FallbackPolicy.NETWORK_ONLY:
        return error_type == "network"
    return False


def classify_fallback_error(error: str) -> str:
    lower = error.lower()
    classes = load_yaml("fallback_routes.yaml").get("error_classes", {})
    quota_needles = [str(needle).lower() for needle in classes.get("quota", [])]
    if any(needle in lower for needle in quota_needles):
        return "quota"
    network_needles = [str(needle).lower() for needle in classes.get("network", [])]
    if any(needle in lower for needle in network_needles):
        return "network"
    return "unknown"


def _with_fallback_reason(primary_source: str, primary_status: SourceStatus, fallback_status: SourceStatus) -> str:
    reason = f"fallback_after={primary_source}; primary_error={primary_status.error}"
    if fallback_status.error:
        return f"{fallback_status.error}; {reason}"
    return reason


def mark_fallback_hits(hits: list[Hit], fallback_from: str) -> None:
    for hit in hits:
        hit.extra["fallback_from"] = fallback_from
        hit.extra["is_fallback_result"] = True


def normalize_hits(q: Query, hits: list[Hit]) -> list[Hit]:
    normalized: list[Hit] = []
    for hit in hits:
        hit.canonical_url = canonicalize_url(hit.url, title=hit.title, published=hit.published_at)
        text = f"{hit.title} {hit.snippet}"
        hit.matched_entities = match_entities(text, q.entities)
        hit.fetched_at = hit.fetched_at or datetime.now(timezone.utc)
        normalized.append(classify_hit(hit))
    return normalized


def dedup_hits(hits: list[Hit]) -> list[Hit]:
    by_url: dict[str, Hit] = {}
    for hit in hits:
        key = hit.canonical_url or canonicalize_url(hit.url, title=hit.title, published=hit.published_at)
        existing = by_url.get(key)
        if existing is None:
            by_url[key] = hit
            continue
        existing.found_by = sorted(set(existing.found_by + hit.found_by))
        if len(hit.snippet) > len(existing.snippet):
            existing.snippet = hit.snippet
        if len(hit.title) > len(existing.title):
            existing.title = hit.title
        if hit.tier > existing.tier:
            existing.tier = hit.tier
        if not existing.published_at and hit.published_at:
            existing.published_at = hit.published_at
        existing.matched_entities = sorted(set(existing.matched_entities + hit.matched_entities))
    return list(by_url.values())


def _normalize_source_name(source: str) -> str:
    source = source.strip()
    aliases = {"wechat": "wechat_opencli", "公众号": "wechat_opencli", "微信手工": "manual_wechat"}
    return aliases.get(source, source)


def _mentions_wechat(text: str) -> bool:
    lower = text.lower()
    return "公众号" in text or "wechat" in lower or "微信" in text
