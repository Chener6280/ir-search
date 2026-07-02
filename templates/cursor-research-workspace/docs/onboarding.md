# Cursor Research Workspace Onboarding

This folder is the generated research workspace. Open this folder alone in Cursor.

## Validate The Workspace

```bash
python3 scripts/validate_workspace.py .
```

Expected result:

```text
[OK] Cursor research workspace validation passed.
```

## MCP Startup

Cursor starts `ir_search` through:

```text
.cursor/mcp.json -> scripts/run_ir_search_mcp.sh
```

The wrapper can source an external env file through `IR_SEARCH_ENV_FILE`. This keeps API keys out of `.cursor/mcp.json`.

## First Prompts

1. `prompts/R-SOURCE-HEALTH.md`
2. `prompts/R-DEEP-RESEARCH-SMOKE.md`
3. `prompts/R-FINANCE-WEB.md`
4. `prompts/R-LITERATURE.md`

If MCP is unavailable, current facts must downgrade to the canonical fallback wording in `.cursor/rules/40-fallback-policy.mdc`.
