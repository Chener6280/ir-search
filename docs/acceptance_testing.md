# Acceptance Testing

This repository includes a deterministic dry-run harness for Cursor research acceptance tests.

This repository is the canonical source for Cursor workspace acceptance cases. The `ir-search` repository may mirror these cases for engine-side dry-runs, but changes to Cursor workspace acceptance behavior should start here.

The harness does not call live MCP tools by itself. It records expected case structure, required tool sequences, and scoring checks so a human or Cursor-run black-box report can be reviewed consistently.

Before a real Cursor black-box run, the generated workspace must pass the MCP runtime preflight:

```bash
python /ABSOLUTE/PATH/TO/cursor-research-workspace/scripts/validate_workspace.py \
  /ABSOLUTE/PATH/TO/cursor-research-workspace \
  --mode generated
```

This validation calls `scripts/doctor_ir_search_mcp.py` unless `--skip-mcp-runtime-check` is explicitly provided. Acceptance results are not considered release evidence when `ir_search` is absent from Cursor's MCP tool list or when the selected `IR_SEARCH_PYTHON` cannot import `mcp.server.fastmcp.FastMCP`.

## Run the Dry Run

```bash
python scripts/run_acceptance_cases.py --dry-run
```

## Score a Saved Report

```bash
python scripts/score_acceptance_results.py tests/fixtures/sample_acceptance_output.md
```

The scorer checks for raw JSON leakage, secret leakage, missing reviewer ratings, missing run IDs for current-information cases, misuse of `source_health` as actual evidence, placeholder/mock evidence misuse, unsupported official-confirmation wording, missing claim status, freshness caveats, and whether `verify_claims` was called for verification cases.
