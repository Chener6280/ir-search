from __future__ import annotations

import os
from typing import Optional, Union

from .adapters.anysearch import AnySearchAdapter
from .adapters.base import SearchAdapter
from .adapters.bocha import BochaAdapter
from .adapters.cninfo import CninfoAdapter
from .adapters.exa import ExaAdapter
from .adapters.mock import MockSearchAdapter
from .adapters.placeholder import PlaceholderAdapter
from .adapters.tavily import TavilyAdapter
from .adapters.wechat_opencli import WechatOpenCLIAdapter
from .cache import CallLogger, FileCache
from .models import Query, SearchResult
from .network import apply_auto_proxy_settings
from .pipeline import run_pipeline


def build_registry(live: Optional[bool] = None) -> dict[str, SearchAdapter]:
    if live is None:
        live = os.environ.get("IR_SEARCH_LIVE") == "1"

    sources = [
        "cninfo",
        "sse",
        "szse",
        "hkex",
        "sec",
        "bocha",
        "exa",
        "company_ir",
        "broker_research",
        "regulator_sites",
        "industry_media",
    ]
    registry: dict[str, SearchAdapter] = {name: MockSearchAdapter(name) for name in sources}
    if live:
        registry["cninfo"] = CninfoAdapter()
        registry["bocha"] = BochaAdapter()
        registry["exa"] = ExaAdapter()
        # Filing searches must not quietly fall back to Bocha as if it were an official disclosure source.
        # Until real exchange / regulator adapters are implemented, live mode marks these sources as placeholders.
        for filing_source in ["sse", "szse", "hkex", "sec"]:
            registry[filing_source] = PlaceholderAdapter(
                filing_source,
                f"{filing_source} live filing adapter is not implemented; do not rely on Bocha for official filings",
            )
    registry["wechat_opencli"] = WechatOpenCLIAdapter()
    registry["tavily"] = TavilyAdapter()
    registry["anysearch"] = AnySearchAdapter(name="anysearch", auth_mode="required")
    registry["web_search"] = AnySearchAdapter(name="web_search", auth_mode="anonymous")
    return registry


def search(
    x: Union[str, Query],
    registry: Optional[dict[str, SearchAdapter]] = None,
    cache: Optional[FileCache] = None,
    logger: Optional[CallLogger] = None,
) -> SearchResult:
    apply_auto_proxy_settings()
    return run_pipeline(x, registry or build_registry(), cache=cache, logger=logger)
