from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ir_search.research import deep_research


def main() -> None:
    root = Path(__file__).resolve().parent
    cases = yaml.safe_load((root / "cases.yaml").read_text(encoding="utf-8"))["cases"]
    report = {"cases": [run_case(case) for case in cases]}
    print(json.dumps(report, ensure_ascii=False, indent=2))


def run_case(case: dict[str, Any]) -> dict[str, Any]:
    run = deep_research(case["question"], max_searches=1, max_documents=4)
    payload = run.to_dict()
    behavior_results = {
        behavior: check_behavior(behavior, payload)
        for behavior in case.get("required_behaviors", [])
    }
    return {
        "id": case["id"],
        "question": case["question"],
        "passed": all(behavior_results.values()),
        "behaviors": behavior_results,
        "run_id": payload["run_id"],
        "n_documents": len(payload["documents_read"]),
        "n_evidence_spans": len(payload["evidence_spans"]),
        "n_claims": len(payload["claim_ledger"]),
        "mock_or_placeholder": sorted(
            {
                item["source"]
                for item in payload["diagnostics"]
                if item.get("adapter_mode") in {"mock", "placeholder"} or item.get("skipped")
            }
        ),
        "unverified_items": payload["unverified_items"],
    }


def check_behavior(behavior: str, payload: dict[str, Any]) -> bool:
    diagnostics = payload["diagnostics"]
    documents = payload["documents_read"]
    evidence = payload["evidence_spans"]
    ledger = payload["claim_ledger"]
    source_matrix = payload["source_matrix"]
    if behavior == "attempt_official_filing":
        return any(item["source"] in {"cninfo", "sse", "szse", "hkex", "sec"} for item in diagnostics)
    if behavior == "attempt_regulator_source":
        return any(item["source"] == "regulator_sites" for item in diagnostics)
    if behavior == "read_documents":
        return bool(documents)
    if behavior == "extract_evidence_spans":
        return bool(evidence)
    if behavior == "show_diagnostics":
        return bool(diagnostics)
    if behavior == "show_source_matrix":
        return bool(source_matrix)
    if behavior == "use_wechat_as_candidate_not_authority":
        return all(entry["status"] != "supported" for entry in ledger) or bool(payload["unverified_items"])
    if behavior == "mark_unverified_if_no_primary_source":
        return bool(payload["unverified_items"]) or any(entry["status"] != "supported" for entry in ledger)
    return False


if __name__ == "__main__":
    main()
