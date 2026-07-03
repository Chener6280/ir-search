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

Bootstrap runs an MCP runtime preflight by default. The selected `--ir-search-python` must be the same Python 3.10+ interpreter Cursor will use, and it must import all of:

- `ir_search`
- `mcp.server.fastmcp.FastMCP`
- `ir_search.mcp_server`

If the preflight fails with a missing MCP dependency, install the MCP extra into that interpreter. The `mcp>=1` package requires Python >=3.10, so do not use macOS's older system Python for Cursor MCP:

```bash
/ABSOLUTE/PATH/TO/python -m pip install -e "/ABSOLUTE/PATH/TO/ir-search[mcp]"
```

You can diagnose an already generated workspace with:

```bash
python /ABSOLUTE/PATH/TO/cursor-research-workspace/scripts/doctor_ir_search_mcp.py \
  --ir-search-python /ABSOLUTE/PATH/TO/python \
  --ir-search-path /ABSOLUTE/PATH/TO/ir-search
```

Use `--skip-mcp-runtime-check` only when packaging the template or creating intentionally offline fixtures. For real Cursor use, a Python payload, terminal import, or manual script run is not a substitute for Cursor listing the `ir_search` MCP tools.

If you already keep provider keys in the `ir_search` repo's `ir_search.env`, pass it with `--env-file`. The generated MCP config points Cursor at `scripts/run_ir_search_mcp.sh`; the wrapper sources the env file at startup, so real keys are not written into `.cursor/mcp.json`.

Use `--dry-run` to preview created files. Existing files are not overwritten unless `--overwrite` is provided.

## Local Keys With .env.local

The generated workspace includes `.env.local.example`. For Cursor GUI launches, prefer workspace-local `.env.local` because Cursor may not inherit your shell environment:

```bash
cd /ABSOLUTE/PATH/TO/cursor-research-workspace
cp .env.local.example .env.local
```

Fill only local values in `.env.local`; never commit it and never paste key values into prompts, reports, or GitHub. The template `.gitignore`, `.cursorignore`, and `.cursorindexingignore` keep `.env.local` out of Git, Cursor context, and indexing while allowing `.env.local.example`.

After editing `.env.local`, reload Cursor or start a new Agent conversation. `scripts/run_ir_search_mcp.sh` sources `.env.local` before starting the MCP server, then `R-SOURCE-HEALTH` should report only `has_KEY=true/false` plus source diagnostics such as `key_missing`, `live_disabled`, `adapter_mock`, `adapter_not_implemented`, or `command_missing`.

## MCP Configuration

The bootstrap script renders:

```text
.cursor/mcp.json.example
.cursor/mcp.json
```

The generated MCP config declares the `ir_search` server as stdio and uses a local wrapper script:

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

If `ir_search` MCP is unavailable, do not answer current facts as if verified. Do not replace the missing MCP connection with a Python payload. State that external verification is required, provide only a conceptual framework, list manual sources to check, and ask the user to run `scripts/doctor_ir_search_mcp.py`.

## Evidence Status Mapping

Use `claim_ledger.status` to decide final wording:

- `supported`: may be stated as fact.
- `mixed`: state with caveats.
- `insufficient_evidence`: do not state as fact.
- `contradicted`: state the contradiction clearly.

## Important Limitation

`ir_search.deep_research` is an evidence orchestration tool. It is not a complete hosted GPT/Claude Deep Research clone. Cursor or another host LLM still writes the final memo from evidence artifacts, diagnostics, source tiers, and claim status.

For current-information questions, call `ir_search.source_health` before `ir_search.deep_research`.
