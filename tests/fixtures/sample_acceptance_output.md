# Cursor Acceptance Sample

## test_01_source_health

- case_id: test_01_source_health
- cursor_self_rating: 4
- reviewer_rating: 4
- tool_calls_observed:
  - ir_search.source_health
- run_id: dr_sample_source_health_001
- used_previous_run: false
- evidence_basis:
  - source_health
  - manual_static_check

检索状态：source_health 显示 cninfo live，sse placeholder。source_health only reports capability; it is not actual evidence.

未验证事项：placeholder 源已披露，不能作为证据。

## test_02_ai_optical_module_demand

- case_id: test_02_ai_optical_module_demand
- cursor_self_rating: 3
- reviewer_rating: 3
- tool_calls_observed:
  - ir_search.source_health
  - ir_search.deep_research
- run_id: dr_20260702_sample
- used_previous_run: false
- evidence_basis:
  - source_health
  - deep_research_run
  - claim_ledger

核心结论：
- [mixed] 海外 AI 光模块需求存在公开证据信号，但 freshness_bucket=recent_30d 的官方证据不足。

证据表：
| Claim | Status | Source | Freshness | Caveat |
|---|---|---|---|---|
| 海外需求信号增强 | mixed | company announcement | recent_30d | official document fetched; media is corroboration only |

未验证事项：placeholder/mock 不作为 evidence，historical 或 missing_date 只能作为背景。

## test_14_verify_claims

- case_id: test_14_verify_claims
- cursor_self_rating: 4
- reviewer_rating: 4
- tool_calls_observed:
  - ir_search.verify_claims
- run_id: dr_verify_claims_sample
- used_previous_run: false
- evidence_basis:
  - verify_claims
  - claim_ledger

证据表：
| Claim | Status | Source |
|---|---|---|
| claim A | supported | filing |
| claim B | contradicted | announcement |
