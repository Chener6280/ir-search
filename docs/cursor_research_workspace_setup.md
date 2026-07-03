# Cursor Research Workspace Setup

This document explains how to create a separate Cursor research workspace that uses `ir_search` as an MCP evidence engine.

## Why A Separate Workspace

Do not use the `ir-search` code repository itself as the daily research workspace. Research Q&A should not be polluted by code files, Git state, dependency files, terminal output, or editor state. Keep the evidence engine and the research workspace separate:

```text
ir-search repository -> tools and MCP server
cursor research workspace -> prompts, notes, sources, outputs
```

## Bootstrap

From the `ir-search` repository:

```bash
python scripts/bootstrap_cursor_research_workspace.py \
  --target /ABSOLUTE/PATH/TO/cursor-research-workspace \
  --ir-search-python /ABSOLUTE/PATH/TO/python \
  --ir-search-path /ABSOLUTE/PATH/TO/ir-search
```

Optional arguments:

```bash
  --ir-search-live 1 \
  --env-file /ABSOLUTE/PATH/TO/ir_search.env \
  --manual-wechat-root /ABSOLUTE/PATH/TO/manual_wechat_articles \
  --cache-dir /ABSOLUTE/PATH/TO/.ir_search_cache
```

`--ir-search-live` defaults to `0` for first-run safety. Rerun with `--ir-search-live 1` after provider keys are configured.

If you already keep provider keys in the `ir_search` repo's `ir_search.env`, pass it with `--env-file`. The generated MCP config points Cursor at `scripts/run_ir_search_mcp.sh`; the wrapper sources the env file at startup, so real keys are not written into `.cursor/mcp.json`.

Use `--dry-run` to preview created files. Existing files are not overwritten unless `--overwrite` is provided.

## MCP Configuration

The bootstrap script renders:

```text
.cursor/mcp.json.example
.cursor/mcp.json
```

The generated MCP config uses a local wrapper script:

```text
.cursor/mcp.json -> scripts/run_ir_search_mcp.sh -> python -m ir_search.mcp_server
```

Do not commit API keys, cookies, tokens, or private credentials. Prefer `--env-file` over putting key values in `.cursor/mcp.json`.

## Validate

After bootstrap:

```bash
python /ABSOLUTE/PATH/TO/cursor-research-workspace/scripts/validate_workspace.py \
  /ABSOLUTE/PATH/TO/cursor-research-workspace \
  --mode generated
```

Expected:

```text
[OK] Cursor research workspace validation passed.
```

## Use In Cursor

Open only the generated research workspace in Cursor. Do not open the `ir-search` code repository in the same window for research Q&A.

Recommended first smoke test:

```text
请调用 ir_search.source_health，告诉我当前哪些 source 是 live、mock、placeholder 或不可用。不要做市场分析。
```

Then test evidence discipline:

```text
[R-FINANCE-WEB]
请调用 ir_search.deep_research，分析最近关于“AI 光模块 海外需求”的公开信息。必须列 diagnostics，不要把 search snippet 当最终证据。
```

Recommended prompt order:

1. `prompts/R-SOURCE-HEALTH.md`
2. `prompts/R-DEEP-RESEARCH-SMOKE.md`
3. `prompts/R-FINANCE-WEB.md`
4. `prompts/R-LITERATURE.md`

## Outputs And Indexing

The generated workspace keeps Cursor indexing narrow:

- `outputs/reports/`, `outputs/memos/`, and `outputs/source_tables/` are intended for reusable research artifacts.
- `outputs/raw/` and `outputs/tmp/` are excluded from indexing for bulky source dumps and scratch files.
- `scripts/validate_workspace.py` is indexed so future maintainers can inspect the workspace checks.

## Fallback When MCP Is Unavailable

If `ir_search` MCP is unavailable, do not answer current facts as if verified. State that external verification is required, provide only a conceptual framework, and list manual sources to check.

## Evidence Status Mapping

Use `claim_ledger.status` to decide final wording:

- `supported`: may be stated as fact.
- `mixed`: state with caveats.
- `insufficient_evidence`: do not state as fact.
- `contradicted`: state the contradiction clearly.

## Important Limitation

`ir_search.deep_research` is an evidence orchestration tool. It is not a complete hosted GPT/Claude Deep Research clone. Cursor or another host LLM still writes the final memo from evidence artifacts, diagnostics, source tiers, and claim status.

For current-information questions, call `ir_search.source_health` before `ir_search.deep_research`.
