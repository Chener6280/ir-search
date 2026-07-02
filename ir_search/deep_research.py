from __future__ import annotations

import argparse
import json

from .research import deep_research


def main() -> None:
    parser = argparse.ArgumentParser(description="Run deterministic IR deep research")
    parser.add_argument("question")
    parser.add_argument("--intent", default="auto")
    parser.add_argument("--freshness", default="30d")
    parser.add_argument("--max-rounds", type=int, default=3)
    parser.add_argument("--max-documents", type=int, default=12)
    args = parser.parse_args()
    run = deep_research(
        args.question,
        intent=args.intent,
        freshness=args.freshness,
        max_rounds=args.max_rounds,
        max_documents=args.max_documents,
    )
    print(json.dumps(run.to_dict(), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
