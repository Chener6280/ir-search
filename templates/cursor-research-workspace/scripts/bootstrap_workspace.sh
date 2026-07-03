#!/usr/bin/env bash
set -euo pipefail

workspace_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

python3 "$workspace_root/scripts/validate_workspace.py" "$workspace_root"

cat <<'EOF'

Next steps in Cursor:
1. Open this folder alone.
2. Run prompts/R-SOURCE-HEALTH.md.
3. If MCP is unavailable, inspect .cursor/mcp.json and scripts/run_ir_search_mcp.sh.
4. Run prompts/R-DEEP-RESEARCH-SMOKE.md after source_health succeeds.
EOF
