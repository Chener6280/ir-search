# Smoke Test Checklist

Canonical checklist: `scripts/smoke_test_checklist.md`.

Recommended first-run order:

0. `docs/onboarding.md`
1. `prompts/R-SOURCE-HEALTH.md`
2. `prompts/R-DEEP-RESEARCH-SMOKE.md`
3. `prompts/R-FINANCE-WEB.md`
4. `prompts/R-LITERATURE.md`

Use `sources/papers/sample_monetary_policy_transmission.md` for local-only literature smoke tests.

Expected behavior summary:

- Source health is reported before current-information research.
- API key values are never printed.
- Search snippets are not treated as final evidence when full documents are available.
- Key claims use `claim_ledger.status`: supported / mixed / insufficient_evidence / contradicted.
- If MCP is unavailable, the answer downgrades to a framework and manual verification checklist.
