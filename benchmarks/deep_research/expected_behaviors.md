# Deep Research Expected Behaviors

The benchmark checks whether `deep_research` behaves more like an evidence engine than a snippet summarizer.

Required behaviors include:

- attempting official or regulator sources when the question asks about filings, earnings, policy, or regulation;
- reading documents, or explicitly marking snippet fallback;
- extracting `EvidenceSpan` records;
- generating a `claim_ledger`;
- exposing diagnostics;
- emitting a `source_matrix`;
- marking unverified items when primary evidence is missing;
- keeping mock and placeholder paths visible.

The MVP benchmark is structural. It does not score financial correctness without live source fixtures.
