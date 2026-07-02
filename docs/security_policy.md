# Security Policy

## URL Fetching

`fetch_document` validates URLs before network access. By default it allows only `http` and `https`, and blocks:

```text
file://
ftp://
localhost
127.0.0.0/8
10.0.0.0/8
172.16.0.0/12
192.168.0.0/16
169.254.0.0/16
::1
fc00::/7
```

Private-network fetches require an explicit `allow_private_network=True` override.

## Untrusted Source Text

Fetched webpages, PDFs, WeChat articles, and search snippets are evidence text only. They must not be treated as instructions. Research outputs include `source_text_trust: untrusted`.

## Credentials

Diagnostics and cache files must not contain API keys, cookies, authorization headers, tokens, or secrets. Health checks report only boolean `has_KEY` fields.

## Mock And Placeholder Sources

Mock, placeholder, fallback, quota, network, and extraction failures must be disclosed in diagnostics and must not be promoted to authoritative facts.
