# Acceptance Testing

This repository includes a deterministic dry-run harness for Cursor research acceptance tests.

The harness does not call live MCP tools by itself. It records expected case structure, required tool sequences, and scoring checks so a human or Cursor-run black-box report can be reviewed consistently.

## Run the Dry Run

```bash
python scripts/run_acceptance_cases.py --dry-run
```

## Score a Saved Report

```bash
python scripts/score_acceptance_results.py tests/fixtures/sample_acceptance_output.md
```

The scorer checks for raw JSON leakage, secret leakage, missing reviewer ratings, missing run IDs for current-information cases, misuse of `source_health` as actual evidence, placeholder/mock evidence misuse, unsupported official-confirmation wording, missing claim status, freshness caveats, and whether `verify_claims` was called for verification cases.
