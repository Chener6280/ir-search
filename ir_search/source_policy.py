"""User-selected source order, distinct from implemented/live capabilities."""
from __future__ import annotations

from dataclasses import dataclass

from .contracts import JsonModel


@dataclass(frozen=True)
class SourceRoute(JsonModel):
    dataset: str
    market: str
    providers: tuple[str, ...]
    horizon: str = "historical_and_recent"


RECENT_INTRADAY_DAYS = 14  # Request guard in calendar days, NOT guaranteed coverage.
_ROUTES = (
    *(SourceRoute(d, "CN_FUND", ("wind_mysql", "jydb") if d == "fund_nav" else ("wind_mysql",)) for d in ("fund_profile", "fund_nav", "fund_shares", "fund_holdings", "fund_exchange_daily")),
    SourceRoute("macro_series", "GLOBAL", ("global_macro",)),
    SourceRoute("derivatives_bars", "CN_FUTURES", ("fiona",), "recent_only"),
    SourceRoute("derivatives_bars", "CN_OPTIONS", ("fiona",), "recent_only"),
    SourceRoute("option_risk", "CN_OPTIONS", ("fiona",)),
    SourceRoute("securities", "US", ("fmp",), "current_snapshot"),
    SourceRoute("prices_daily_basic", "US", ("fmp",), "bounded_vendor_history"),
    SourceRoute("financial_statements_standardized", "US", ("fmp",), "bounded_vendor_history"),
    SourceRoute("securities", "A_SHARE", ("wind_mysql", "jydb")),
    SourceRoute("prices_daily", "A_SHARE", ("wind_mysql", "jydb")),
    SourceRoute("futures_daily", "CN_FUTURES", ("wind_mysql", "jydb")),
    SourceRoute("options_daily", "CN_OPTIONS", ("wind_mysql", "jydb")),
    SourceRoute("futures_contracts", "CN_FUTURES", ("wind_mysql", "jydb")),
    SourceRoute("options_contracts", "CN_OPTIONS", ("wind_mysql", "jydb")),
    SourceRoute("trading_calendar", "CN_FUTURES", ("wind_mysql",)),
    SourceRoute("derivatives_daily", "CN_DERIVATIVES", ("wind_mysql", "jydb")),
    SourceRoute("financial_statements", "A_SHARE", ("wind_mysql", "jydb")),
    SourceRoute("prices_intraday", "A_SHARE", ("akshare",), "recent_only"),
    SourceRoute("futures_intraday", "CN_FUTURES", ("akshare",), "recent_only"),
    SourceRoute("options_intraday", "CN_OPTIONS", ("akshare",), "recent_only"),
)


def _route_for(request):
    return next((route for route in _ROUTES
                 if route.dataset == request.dataset and route.market == request.market), None)


def source_policy() -> dict:
    """Describe intent; a route never implies an implemented or authorized adapter."""
    return {
        "routes": [route.to_dict() for route in _ROUTES],
        "verification_basis": "user_selected_policy_not_capability",
        "historical_intraday_provider": None,
        "recent_intraday_max_calendar_days": RECENT_INTRADAY_DAYS,
        "fallback_on": ["provider_not_registered", "coverage_not_supported", "no_rows", "not_found"],
        "fallback_unit": "whole_request_no_row_or_field_merging",
        "fallback_on_operational_errors": False,
        "explicit_provider_disables_fallback": True,
    }
