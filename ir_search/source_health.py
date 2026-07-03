from __future__ import annotations

import json
import os

from .adapters.manual_wechat import manual_wechat_root
from .kernel import build_registry


def source_health() -> dict:
    """Return adapter mode, credential presence, and placeholder/mock visibility."""

    live_enabled = os.environ.get("IR_SEARCH_LIVE") == "1"
    registry = build_registry()
    sources: dict[str, dict] = {}
    for name, adapter in sorted(registry.items()):
        mode = getattr(adapter, "mode", "unknown")
        notes: list[str] = []
        reasons: list[str] = []
        ok = mode == "live"
        required_env = REQUIRED_ENV.get(name)
        if not live_enabled and name in LIVE_GATED_SOURCES:
            ok = False
            reasons.append("live_disabled")
            notes.append("IR_SEARCH_LIVE is not 1; live provider disabled")
        if mode == "mock":
            ok = False
            reason = "live_disabled" if name in LIVE_GATED_SOURCES and not live_enabled else "adapter_mock"
            if reason not in reasons:
                reasons.append(reason)
            notes.append("mock adapter; useful for routing tests but not authoritative")
        elif mode == "placeholder":
            ok = False
            reasons.append("adapter_not_implemented")
            notes.append(getattr(adapter, "message", "placeholder adapter is not implemented"))
        elif required_env and not os.environ.get(required_env):
            ok = False
            reason = "command_missing" if name in COMMAND_REQUIRED_SOURCES else "key_missing"
            reasons.append(reason)
            notes.append(f"{required_env} is not set")
        if name == "manual_wechat":
            root = manual_wechat_root()
            if not root.exists():
                ok = False
                reasons.append("path_missing")
                notes.append("manual wechat directory not found; set MANUAL_WECHAT_ROOT")
        if not reasons and mode == "experimental":
            reasons.append("experimental_adapter")
        elif not reasons and mode == "fallback":
            reasons.append("fallback_adapter")
        if not reasons and not ok:
            reasons.append("adapter_error")
        if not reasons and ok:
            reasons.append("available")
        sources[name] = {
            "adapter_mode": mode,
            "ok": ok,
            "availability_reason": reasons[0],
            "diagnostics": {
                "reasons": reasons,
                "required_env": required_env,
                "has_required_env": bool(os.environ.get(required_env)) if required_env else None,
                "live_enabled": live_enabled,
            },
            "notes": notes,
        }
    return {
        "sources": sources,
        "env": {
            "IR_SEARCH_LIVE": os.environ.get("IR_SEARCH_LIVE", "0"),
            "has_BOCHA_API_KEY": bool(os.environ.get("BOCHA_API_KEY")),
            "has_EXA_API_KEY": bool(os.environ.get("EXA_API_KEY")),
            "has_TAVILY_API_KEY": bool(os.environ.get("TAVILY_API_KEY")),
            "has_ANYSEARCH_API_KEY": bool(os.environ.get("ANYSEARCH_API_KEY")),
            "has_DAJIALA_KEY": bool(os.environ.get("DAJIALA_KEY")),
            "has_ZSXQ_GROUP_IDS": bool(os.environ.get("ZSXQ_GROUP_IDS")),
            "has_WECHAT_OPENCLI_COMMAND": bool(os.environ.get("WECHAT_OPENCLI_COMMAND")),
            "has_MANUAL_WECHAT_ROOT": bool(os.environ.get("MANUAL_WECHAT_ROOT") or os.environ.get("IR_SEARCH_MANUAL_WECHAT_ROOT")),
        },
        "source_text_trust": "untrusted",
    }


def main() -> None:
    print(json.dumps(source_health(), ensure_ascii=False, indent=2))


REQUIRED_ENV = {
    "bocha": "BOCHA_API_KEY",
    "exa": "EXA_API_KEY",
    "tavily": "TAVILY_API_KEY",
    "anysearch": "ANYSEARCH_API_KEY",
    "dajiala": "DAJIALA_KEY",
    "wechat_opencli": "WECHAT_OPENCLI_COMMAND",
    "zsxq": "ZSXQ_GROUP_IDS",
}

COMMAND_REQUIRED_SOURCES = {"wechat_opencli"}

LIVE_GATED_SOURCES = {
    "anysearch",
    "bocha",
    "cninfo",
    "dajiala",
    "exa",
    "hkex",
    "sec",
    "sse",
    "szse",
    "tavily",
    "wechat_opencli",
    "web_search",
    "zsxq",
}


if __name__ == "__main__":
    main()
