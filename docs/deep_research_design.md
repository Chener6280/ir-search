# Deep Research Design

`ir_search.research.deep_research` is a bounded deterministic workflow for local investment research.

## Flow

1. Plan bounded queries from the question.
2. Call the existing deterministic `search` pipeline.
3. Fetch top documents when possible.
4. Fall back to marked search-hit snippet documents only when the hit is mock or full fetch fails.
5. Extract question-relevant `EvidenceSpan` records.
6. Draft deterministic claim candidates from top spans.
7. Verify claims against extracted evidence.
8. Build `source_matrix`, diagnostics, unverified items, and a finance memo.

## Budgets

The orchestrator clamps:

```text
max_rounds <= 3
max_searches <= 8
max_documents <= 12
```

## Current MVP Limits

- No LLM is used.
- PDF extraction requires optional `ir-search[extract]`.
- Snippet fallback is explicitly marked and should not be treated as full-document evidence.
- Mock and placeholder diagnostics are preserved in the final answer.
