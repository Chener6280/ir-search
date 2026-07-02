# Prompt Index

Recommended first-use order:

1. `R-SOURCE-HEALTH.md`: check whether live, mock, placeholder, or unavailable sources are active.
2. `R-DEEP-RESEARCH-SMOKE.md`: run a small end-to-end evidence orchestration smoke test.
3. `R-FINANCE-WEB.md`: use for current finance, market, company, policy, broker, or WeChat research.
4. `R-LITERATURE.md`: use for local-only / document-first paper, report, or note reading. For smoke tests, reference `sources/papers/sample_monetary_policy_transmission.md`.
5. `R-VERIFY-CLAIMS.md`: turn a draft into verifiable claims and check them with `ir_search`.
6. `R-LATEST-GUARD.md`: use when a question may depend on current facts.

Canonical policy sources:

- Claim status mapping: `.cursor/rules/20-ir-search-evidence-policy.mdc`.
- Fallback wording: `.cursor/rules/40-fallback-policy.mdc`.

Keep task prompts focused on task orchestration. Do not duplicate the full alwaysApply policy text here.
