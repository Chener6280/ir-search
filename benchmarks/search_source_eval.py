from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ir_search import Query, search


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", default=str(Path(__file__).with_name("queries_investment_research.yaml")))
    parser.add_argument("--source", action="append", required=True)
    args = parser.parse_args()

    queries = yaml.safe_load(Path(args.queries).read_text(encoding="utf-8"))["queries"]
    rows = []
    for text in queries:
        q = Query(text=text, sources=args.source, count=10)
        started = time.perf_counter()
        result = search(q)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        rows.append(
            {
                "query": text,
                "n_hits": len(result.hits),
                "top5_valid_rate": _valid_rate(result.hits[:5]),
                "top10_valid_rate": _valid_rate(result.hits[:10]),
                "source_failure_rate": len(result.failed_sources) / max(1, len(result.diagnostics)),
                "duplicate_rate": _duplicate_rate(result.hits),
                "published_at_coverage": sum(1 for h in result.hits if h.published_at) / max(1, len(result.hits)),
                "avg_latency_ms": elapsed_ms,
            }
        )
    print(json.dumps(rows, ensure_ascii=False, indent=2))


def _valid_rate(hits) -> float:
    return sum(1 for hit in hits if hit.title and hit.url) / max(1, len(hits))


def _duplicate_rate(hits) -> float:
    urls = [hit.canonical_url for hit in hits]
    return 1.0 - len(set(urls)) / max(1, len(urls))


if __name__ == "__main__":
    main()
