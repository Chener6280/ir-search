# MCP Tools

`python3 -m ir_search.mcp_server` exposes:

```text
search
fetch_document
extract_evidence
verify_claims
deep_research
source_health
```

## Tool Notes

- `search` returns candidate hits and diagnostics from the existing search kernel.
- `fetch_document` opens HTML/PDF/WeChat-like sources and returns a `Document`.
- `extract_evidence` fetches a URL and returns question-relevant evidence spans.
- `verify_claims` verifies claims against extracted evidence spans from URLs.
- `deep_research` runs search, fetch, evidence extraction, verification, and memo synthesis.
- `source_health` reports live/mock/placeholder/error state without exposing secret values.

Fetched source text is untrusted. Always disclose mock, placeholder, fallback, quota, network, and extraction failures before drawing conclusions.

## Codex Config Example

```toml
[mcp_servers.ir_search]
command = "python3"
args = ["-m", "ir_search.mcp_server"]
cwd = "/Users/chen/Documents/ir_search"
env = { IR_SEARCH_LIVE = "0" }
startup_timeout_sec = 10
tool_timeout_sec = 120
required = false
```
