from __future__ import annotations

from pathlib import Path

from scripts.run_acceptance_cases import load_cases, render_dry_run
from scripts.score_acceptance_results import score_report


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_acceptance_cases_are_machine_readable():
    cases = load_cases(REPO_ROOT / "tests" / "acceptance_cases.yaml")

    assert len(cases) >= 3
    assert cases[0]["id"] == "test_01_source_health"
    assert "ir_search.source_health" in cases[0]["required_tool_sequence"]


def test_acceptance_dry_run_lists_required_fields():
    cases = load_cases(REPO_ROOT / "tests" / "acceptance_cases.yaml")
    output = render_dry_run(cases)

    for phrase in ["case_id:", "cursor_self_rating:", "reviewer_rating:", "tool_calls_observed:", "used_previous_run:"]:
        assert phrase in output


def test_acceptance_scorer_detects_expected_passes():
    scores = score_report(REPO_ROOT / "tests" / "fixtures" / "sample_acceptance_output.md")
    by_id = {score.case_id: score for score in scores}

    assert by_id["test_01_source_health"].required_assertions["source_health_not_actual_evidence"] == "pass"
    assert by_id["test_02_ai_optical_module_demand"].required_assertions["claim_status_present"] == "pass"
    assert by_id["test_02_ai_optical_module_demand"].required_assertions["freshness_caveat_present"] == "pass"
    assert by_id["test_14_verify_claims"].required_assertions["verify_claims_called"] == "pass"
