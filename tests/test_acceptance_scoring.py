from __future__ import annotations

from pathlib import Path

from scripts.run_acceptance_cases import load_cases, render_dry_run
from scripts.score_acceptance_results import score_report


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_acceptance_cases_are_machine_readable():
    cases = load_cases(REPO_ROOT / "tests" / "acceptance_cases.yaml")

    assert len(cases) >= 20
    assert cases[0]["id"] == "test_01_source_health"
    assert "ir_search.source_health" in cases[0]["required_tool_sequence"]
    for case in cases:
        for key in ["id", "mode", "category", "requires_mcp", "question", "required_tool_sequence", "required_output_sections", "assertions", "must_not"]:
            assert key in case, case["id"]


def test_acceptance_dry_run_lists_required_fields():
    cases = load_cases(REPO_ROOT / "tests" / "acceptance_cases.yaml")
    output = render_dry_run(cases)

    for phrase in ["case_id:", "cursor_self_rating:", "reviewer_rating:", "tool_calls_observed:", "used_previous_run:"]:
        assert phrase in output
    assert "must_not:" in output


def test_acceptance_scorer_detects_expected_passes():
    scores = score_report(REPO_ROOT / "tests" / "fixtures" / "sample_acceptance_output.md")
    by_id = {score.case_id: score for score in scores}

    assert by_id["test_01_source_health"].required_assertions["source_health_not_actual_evidence"] == "pass"
    assert by_id["test_02_ai_optical_module_demand"].required_assertions["claim_status_present"] == "pass"
    assert by_id["test_02_ai_optical_module_demand"].required_assertions["freshness_bucket_present"] == "pass"
    assert by_id["test_02_ai_optical_module_demand"].required_assertions["official_gap_report_present"] == "pass"
    assert by_id["test_02_ai_optical_module_demand"].required_assertions["official_gap_report_required_for_claims_present"] == "pass"
    assert by_id["test_02_ai_optical_module_demand"].required_assertions["actual_evidence_by_source_present"] == "pass"
    assert by_id["test_02_ai_optical_module_demand"].required_assertions["reserved_parameters_present"] == "pass"
    assert by_id["test_02_ai_optical_module_demand"].required_assertions["media_not_financial_report"] == "pass"
    assert by_id["test_14_verify_claims"].required_assertions["verify_claims_called"] == "pass"


def test_current_info_reused_run_cannot_pass():
    scores = score_report(REPO_ROOT / "tests" / "fixtures" / "reused_run_output.md")
    by_id = {score.case_id: score for score in scores}

    assert by_id["test_02_ai_optical_module_demand"].used_previous_run is True
    assert by_id["test_02_ai_optical_module_demand"].required_assertions["independent_run_required"] == "fail"
    assert by_id["test_02_ai_optical_module_demand"].required_assertions["used_previous_run_forbidden"] == "fail"


def test_missing_run_id_cannot_pass_current_info():
    scores = score_report(REPO_ROOT / "tests" / "fixtures" / "missing_run_id_output.md")
    by_id = {score.case_id: score for score in scores}

    assert by_id["test_05_ai_optical_module_current_info"].required_assertions["independent_run_required"] == "fail"
    assert by_id["test_05_ai_optical_module_current_info"].required_assertions["run_id_present"] == "fail"


def test_scorer_flags_media_financial_report_and_missing_official_gap():
    path = REPO_ROOT / "tests" / "fixtures" / "_tmp_bad_media.md"
    path.write_text(
        """
## test_bad_media
- case_id: test_bad_media
- category: current_info
- reviewer_rating: 4
- tool_calls_observed:
  - ir_search.deep_research
- run_id: dr_bad_media
- used_previous_run: false
| Claim | Status | Source | Source Tier | Evidence Type |
|---|---|---|---|---|
| claim | supported | stcn.com | MEDIA | financial_report |
""",
        encoding="utf-8",
    )
    try:
        score = score_report(path)[0]
    finally:
        path.unlink()

    assert score.required_assertions["media_not_financial_report"] == "fail"
    assert score.required_assertions["official_gap_report_present"] == "fail"
