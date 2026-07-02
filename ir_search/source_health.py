from __future__ import annotations

import json
import os

from .adapters.manual_wechat import manual_wechat_root
from .kernel import build_registry


def source_health() -> dict:
    """Return adapter mode, credential presence, and placeholder/mock visibility."""

    registry = build_registry()
    sources: dict[str, dict] = {}
    for name, adapter in sorted(registry.items()):
        mode = getattr(adapter, "mode", "unknown")
        notes: list[str] = []
        ok = mode == "live"
        if mode == "mock":
            ok = False
            notes.append("mock adapter; useful for routing tests but not authoritative")
        elif mode == "placeholder":
            ok = False
            notes.append(getattr(adapter, "message", "placeholder adapter is not implemented"))
        elif name in REQUIRED_ENV and not os.environ.get(REQUIRED_ENV[name]):
            ok = False
            notes.append(f"{REQUIRED_ENV[name]} is not set")
        if name == "manual_wechat":
            root = manual_wechat_root()
            if not root.exists():
                ok = False
                notes.append(f"manual wechat directory not found: {root}")
        sources[name] = {
            "adapter_mode": mode,
            "ok": ok,
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


if __name__ == "__main__":
    main()
