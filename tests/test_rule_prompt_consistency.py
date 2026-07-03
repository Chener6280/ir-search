from __future__ import annotations

from pathlib import Path


TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "templates" / "cursor-research-workspace"
STATUSES = ["supported", "mixed", "insufficient_evidence", "contradicted"]


def test_literature_prompt_and_policy_have_local_only_exception():
    policy = (TEMPLATE_ROOT / ".cursor" / "rules" / "20-ir-search-evidence-policy.mdc").read_text(encoding="utf-8")
    literature = (TEMPLATE_ROOT / "prompts" / "R-LITERATURE.md").read_text(encoding="utf-8")

    assert "Local-only and literature exceptions" in policy
    assert "R-LITERATURE" in policy
    assert "local-only / document-first" in literature
    assert "外部佐证" in literature


def test_claim_status_mapping_is_canonical_and_referenced():
    policy = (TEMPLATE_ROOT / ".cursor" / "rules" / "20-ir-search-evidence-policy.mdc").read_text(encoding="utf-8")
    prompts_readme = (TEMPLATE_ROOT / "prompts" / "README.md").read_text(encoding="utf-8")

    assert "source of truth for claim status wording" in policy
    assert "Claim status mapping" in prompts_readme
    for status in STATUSES:
        assert status in policy


def test_prompts_use_claim_status_not_parallel_taxonomies():
    finance = (TEMPLATE_ROOT / "prompts" / "R-FINANCE-WEB.md").read_text(encoding="utf-8")
    latest = (TEMPLATE_ROOT / "prompts" / "R-LATEST-GUARD.md").read_text(encoding="utf-8")

    assert "claim_ledger.status" in finance
    assert "claim_ledger.status" in latest
    assert "直接证据支持的事实" not in finance
    assert "合理推断" not in finance


def test_output_format_is_chinese_and_fallback_is_canonical():
    output_rule = (TEMPLATE_ROOT / ".cursor" / "rules" / "30-output-format.mdc").read_text(encoding="utf-8")
    fallback_rule = (TEMPLATE_ROOT / ".cursor" / "rules" / "40-fallback-policy.mdc").read_text(encoding="utf-8")
    latest = (TEMPLATE_ROOT / "prompts" / "R-LATEST-GUARD.md").read_text(encoding="utf-8")

    assert "仅输出编号结论" in output_rule
    assert "R-LITERATURE" in output_rule
    assert "R-SOURCE-HEALTH" in output_rule
    assert "解释机制、含义、替代解释和证据边界" in output_rule
    assert "canonical source of truth for fallback wording" in fallback_rule
    assert "我无法从当前可用工具中取得足够可核验证据" in latest
    assert "以下回答基于已有知识和已提供材料" not in latest


def test_literature_sample_supports_local_only_smoke():
    sample = (TEMPLATE_ROOT / "sources" / "papers" / "sample_monetary_policy_transmission.md").read_text(encoding="utf-8")
    checklist = (TEMPLATE_ROOT / "scripts" / "smoke_test_checklist.md").read_text(encoding="utf-8")
    notes_checklist = (TEMPLATE_ROOT / "notes" / "smoke_test_checklist.md").read_text(encoding="utf-8")

    assert "synthetic" in sample
    assert "difference-in-differences" in sample
    assert "sample_monetary_policy_transmission.md" in checklist
    assert "Canonical checklist" in notes_checklist


def test_claim_status_language_prevents_unsupported_fact_writing():
    policy = (TEMPLATE_ROOT / ".cursor" / "rules" / "20-ir-search-evidence-policy.mdc").read_text(encoding="utf-8")
    output_rule = (TEMPLATE_ROOT / ".cursor" / "rules" / "30-output-format.mdc").read_text(encoding="utf-8")
    verify_prompt = (TEMPLATE_ROOT / "prompts" / "R-VERIFY-CLAIMS.md").read_text(encoding="utf-8")

    assert "insufficient_evidence: do not state as fact" in policy
    assert "contradicted: state the contradiction clearly" in policy
    assert "不得替代 claim status" in output_rule
    assert "不能单独升级为官方事实" in verify_prompt
