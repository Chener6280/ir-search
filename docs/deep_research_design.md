# Deep Research Design

`ir_search.research.deep_research` is a bounded deterministic evidence orchestration workflow for local investment research. It is evidence engine v0, not a complete GPT/Claude hosted Deep Research clone.

## Flow

1. Plan bounded queries from the question.
2. Call the existing deterministic `search` pipeline.
3. Fetch top documents when possible.
4. Fall back to marked search-hit snippet documents only when the hit is mock or full fetch fails.
5. Extract question-relevant `EvidenceSpan` records.
6. Draft deterministic claim candidates from the question and intent templates, then add evidence-derived subclaims.
7. Verify claims against extracted evidence.
8. Run official-only second-pass searches when current-information, company, filing, demand, or public-evidence questions lack primary source evidence.
9. Build `source_matrix`, `official_source_attempts`, `official_gap_report`, diagnostics, unverified items, and a deterministic memo scaffold for the host LLM.

## Official-First Current Information

- Current-information questions include latest/recent/current/近30天/近90天/2026 wording.
- Current company, demand, order, supply-chain, filing, or public-evidence questions require official-source attempts, not only commercial search hits.
- If the question involves AI optical module overseas demand, the planner deterministically adds official-check queries for Coherent, Lumentum, Fabrinet, and Microsoft/Meta/Google/Amazon capex/networking.
- If the same question has China-listed or A-share supply-chain context, `cninfo` is included in required official sources when available.
- `official_source_attempts` and `official_gap_report.actual_retrieval` must include a structured row for every required official source, including 0-hit rows with `official_attempted`, `fetched_documents`, `evidence_spans`, and `reason`.
- `historical` and `missing_date` evidence buckets are background only for current-information claims; only `recent_30d` and `recent_90d` can support those claims.

## Budgets

The orchestrator clamps:

```text
max_rounds <= 3
max_searches <= 8
max_documents <= 12
```

## Current MVP Limits

- No LLM is used.
- Current-information research should call `source_health` before `deep_research`.
- PDF extraction requires optional `ir-search[extract]`.
- Snippet fallback is explicitly marked and should not be treated as full-document evidence.
- Mock and placeholder diagnostics are preserved in the final answer. If sources such as `sse`, `szse`, `hkex`, `sec`, or `company_ir` are mock/placeholder, the user-visible output must say that authoritative full text was not obtained.
- Only `supported` claims should be written as facts. `mixed` and `insufficient_evidence` claims must remain labeled as uncertain or unverified.
