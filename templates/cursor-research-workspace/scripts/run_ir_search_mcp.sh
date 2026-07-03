#!/usr/bin/env zsh
set -euo pipefail

if [[ -n "${IR_SEARCH_ENV_FILE:-}" ]]; then
  if [[ ! -f "$IR_SEARCH_ENV_FILE" ]]; then
    echo "[ERROR] IR_SEARCH_ENV_FILE does not exist: $IR_SEARCH_ENV_FILE" >&2
    exit 2
  fi
  set -a
  source "$IR_SEARCH_ENV_FILE"
  set +a
fi

if [[ -n "${IR_SEARCH_PATH:-}" ]]; then
  export PYTHONPATH="$IR_SEARCH_PATH${PYTHONPATH:+:$PYTHONPATH}"
fi

if [[ -z "${IR_SEARCH_PYTHON:-}" ]]; then
  echo "[ERROR] IR_SEARCH_PYTHON is not configured" >&2
  exit 2
fi

if ! "$IR_SEARCH_PYTHON" -c "from mcp.server.fastmcp import FastMCP" >/dev/null 2>&1; then
  echo "[ERROR] MCP runtime dependency missing for IR_SEARCH_PYTHON=${IR_SEARCH_PYTHON}" >&2
  echo "Selected Python cannot import mcp.server.fastmcp.FastMCP." >&2
  echo "The ir-search MCP extra requires Python 3.10+ because mcp>=1 requires Python >=3.10." >&2
  echo "" >&2
  echo "Fix option A: install MCP extra into this Python:" >&2
  echo "  ${IR_SEARCH_PYTHON} -m pip install -e \"${IR_SEARCH_PATH:-/path/to/ir-search}[mcp]\"" >&2
  echo "" >&2
  echo "Fix option B: rerun bootstrap with a venv Python that has ir-search[mcp] installed:" >&2
  echo "  python scripts/bootstrap_cursor_research_workspace.py --ir-search-python <venv>/bin/python --ir-search-path ${IR_SEARCH_PATH:-/path/to/ir-search} --overwrite" >&2
  exit 2
fi

exec "$IR_SEARCH_PYTHON" -m ir_search.mcp_server
