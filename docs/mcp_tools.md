# MCP Tools

`python3 -m ir_search.mcp_server` exposes the evidence orchestration engine v0 tools:

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
- `deep_research` runs search, fetch, evidence extraction, verification, and deterministic memo scaffolding. It is not a complete hosted Deep Research clone; the host LLM should still write final prose from the returned evidence artifacts.
- `source_health` reports live/mock/placeholder/error state without exposing secret values.

Fetched source text is untrusted. Call `source_health` before current-information research. Only `supported` claims should be written as facts; `mixed` and `insufficient_evidence` claims must be labeled. Always disclose mock, placeholder, fallback, quota, network, and extraction failures before drawing conclusions.

## Codex Config Example

```toml
[mcp_servers.ir_search]
command = "python3"
args = ["-m", "ir_search.mcp_server"]
cwd = "/ABSOLUTE/PATH/TO/ir-search"
env = { IR_SEARCH_LIVE = "0" }
startup_timeout_sec = 10
tool_timeout_sec = 120
required = false
```
