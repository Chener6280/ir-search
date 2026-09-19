"""Isolate optional AKShare SDK calls so a request deadline can terminate them."""
from __future__ import annotations

import contextlib
import json
import os
import sys


def _execute(payload):
    allowed = {"stock_zh_a_hist_min_em", "stock_zh_a_minute", "futures_zh_minute_sina", "option_sse_minute_sina"}
    if not isinstance(payload, dict) or set(payload) != {"function", "kwargs"} or payload["function"] not in allowed:
        return {"error": "unsupported"}
    try:
        # Third-party imports and progress/log output must never reach MCP stdout.
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            import akshare
            frame = getattr(akshare, payload["function"])(**payload["kwargs"])
            if len(frame) > 10000:
                return {"error": "upstream_schema"}
            payload = frame.to_json(orient="records", date_format="iso", double_precision=15)
            if len(payload)>3900000:
                return {"error":"upstream_schema"}
            records = json.loads(payload)
        return {"records": records}
    except ImportError:
        return {"error": "dependency_missing"}
    except Exception as exc:
        # Class names only for classification; exception messages/URLs are discarded.
        name = type(exc).__name__
        if name in {"Timeout", "ReadTimeout", "ConnectTimeout"}:
            return {"error": "timeout"}
        if name in {"ConnectionError", "ProxyError", "HTTPError"}:
            return {"error": "network"}
        if name == "SSLError":
            return {"error": "tls_error"}
        return {"error": "upstream_schema"}


def _main():
    try:
        payload = json.loads(sys.stdin.read(8192))
        result = _execute(payload)
    except Exception:
        result = {"error": "upstream_schema"}
    sys.stdout.write(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    _main()
