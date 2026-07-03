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

exec "$IR_SEARCH_PYTHON" -m ir_search.mcp_server
