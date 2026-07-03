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
8. Build `source_matrix`, diagnostics, unverified items, and a deterministic memo scaffold for the host LLM.

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
