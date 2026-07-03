# Evidence Schema

`ir-search` now models research as an auditable chain:

```text
Hit -> Document -> EvidenceSpan -> ClaimVerification -> ResearchRun
```

## Document

`Document` lives in `ir_search.documents.models`. It stores fetched source text, canonical URL, title, source tier, evidence type, hashes, warnings, errors, and extraction metadata. Source text is always treated as untrusted.

## EvidenceSpan

`EvidenceSpan` lives in `ir_search.evidence.models`. It is a citeable span extracted from a `Document` for a specific question. Each span includes `doc_id`, URL, source, source tier, evidence type, relevance score, character offsets, and optional page/section metadata.

## ClaimVerification

`ClaimVerification` records one claim's deterministic evidence status:

```text
supported | mixed | contradicted | insufficient_evidence
```

Media, broker, WeChat, and social evidence can support discovery, but official filings, regulators, exchanges, and company IR are preferred for primary confirmation.

## ResearchRun

`ResearchRun` lives in `ir_search.research.schemas`. It preserves the search log, documents read, evidence spans, claim ledger, source matrix, diagnostics, unverified items, and final memo text.
