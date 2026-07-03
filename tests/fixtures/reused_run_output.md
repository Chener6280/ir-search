# Cursor Acceptance Reused Run Fixture

## test_02_ai_optical_module_demand

- case_id: test_02_ai_optical_module_demand
- category: current_info
- cursor_self_rating: 4
- reviewer_rating: 4
- tool_calls_observed:
  - ir_search.source_health
- run_id: dr_20260702_073458_6a56f14278
- used_previous_run: true
- evidence_basis:
  - source_health

本题基于 Test 02 run 的历史结果继续分析，未重新调用 ir_search.deep_research。

## test_05_recent_30d_freshness

- case_id: test_05_recent_30d_freshness
- category: current_info
- cursor_self_rating: 3
- reviewer_rating: 3
- tool_calls_observed:
  - ir_search.deep_research
- run_id: missing
- used_previous_run: false
- evidence_basis:
  - deep_research_run

证据表：
| Claim | Status | Source | Freshness |
|---|---|---|---|
| 最近 30 天订单增长 | supported | media | missing_date |
