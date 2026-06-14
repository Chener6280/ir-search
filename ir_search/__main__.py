from __future__ import annotations

import argparse
import json

from .cache import CallLogger, FileCache
from .kernel import search
from .models import FallbackPolicy, Intent, Query, TimeWindow


def main() -> None:
    parser = argparse.ArgumentParser(description="Deterministic investment research search")
    parser.add_argument("query")
    parser.add_argument("--source", "--sources", dest="sources", default=None)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--freshness", "--window", dest="window", default="noLimit")
    parser.add_argument("--allow-browser-fallback", action="store_true")
    parser.add_argument("--intent", choices=[intent.value for intent in Intent], default=None)
    parser.add_argument("--fallback-policy", choices=[policy.value for policy in FallbackPolicy], default="none")
    parser.add_argument("--fallback-on-empty", action="store_true")
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--cache-prune-days", type=int, default=None)
    parser.add_argument("--log", default=None)
    args = parser.parse_args()

    sources = [item.strip() for item in args.sources.split(",")] if args.sources else None
    q = Query(
        text=args.query,
        sources=sources,
        count=args.count,
        window=TimeWindow(raw=args.window),
        allow_browser_fallback=args.allow_browser_fallback,
        intent=Intent(args.intent) if args.intent else Intent.GENERAL,
        allow_fallback=args.fallback_policy != FallbackPolicy.NONE.value,
        fallback_policy=FallbackPolicy(args.fallback_policy),
        fallback_on_empty=args.fallback_on_empty,
    )
    cache = FileCache(args.cache_dir) if args.cache_dir else None
    if cache and args.cache_prune_days is not None:
        cache.prune(args.cache_prune_days)
    logger = CallLogger(args.log) if args.log else None
    result = search(q, cache=cache, logger=logger)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
