# Review Fix Baseline

- Base branch: `codex/update-ir-search-sources`
- Base commit: `8c4225e Add deep research evidence engine`
- Date: 2026-07-02
- pytest result: `203 passed, 2 skipped`
- benchmark result: `benchmarks/deep_research/run_benchmark.py` passed all 3 structural cases

## Known P0 Issues

- `looks_contradictory` has false positives from single-character Chinese negation substring matches.
- `MCP_INSTRUCTIONS` is defined but not exposed to MCP clients.
- Evidence span input parsing is fragile and can raise on malformed tool input.
- Redirect handling can follow a public URL to localhost or private-network targets.

## Known P1 Issues

- Claim candidates are generated from evidence first sentences, which can create self-verifying claims.
- Planner does not strongly use intent.
- Confidence does not reward independent sources.
- Orchestrator, planner, and synthesizer test coverage is thin.
