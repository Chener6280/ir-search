"""Operation-specific registration, separate from the legacy search registry."""
from __future__ import annotations

from typing import Protocol

from .context import RequestContext
from .contracts import DataCapability, DataPage, DataRequest, DatasetDefinition, FieldDefinition, Diagnostic
from .models import FailureKind


class DataAdapterError(Exception):
    """Safe typed errors; raw upstream exception text never crosses this boundary."""
    KINDS = {
        'xhs_login_required': FailureKind.NO_CREDENTIAL,
        'xhs_backend_unavailable': FailureKind.NETWORK,
        'xhs_backend_error': FailureKind.UPSTREAM_SCHEMA,
        'xhs_reference_unavailable': FailureKind.UNIMPLEMENTED,
        'xhs_cache_unavailable': FailureKind.BLOCKED_BY_POLICY,
        'xhs_cache_invalid': FailureKind.BLOCKED_BY_POLICY,
        'gangtise_login_challenge': FailureKind.BLOCKED_BY_POLICY,
        'gangtise_login_busy': FailureKind.BLOCKED_BY_POLICY,
        'gangtise_state_unavailable': FailureKind.BLOCKED_BY_POLICY,
        'gangtise_state_invalid': FailureKind.BLOCKED_BY_POLICY,
        'gangtise_call_budget_exhausted': FailureKind.BUDGET_EXHAUSTED,
        'gangtise_format_unavailable': FailureKind.UNIMPLEMENTED,
        'alphapai_login_challenge': FailureKind.BLOCKED_BY_POLICY,
        'alphapai_call_budget_exhausted': FailureKind.BUDGET_EXHAUSTED,
        'alphapai_cache_unavailable': FailureKind.BLOCKED_BY_POLICY,
        'alphapai_cache_invalid': FailureKind.BLOCKED_BY_POLICY,
        'fiona_call_budget_exhausted': FailureKind.BUDGET_EXHAUSTED,
        'sec_contact_required': FailureKind.NO_CREDENTIAL,
        'sec_access_blocked': FailureKind.BLOCKED_BY_POLICY,
        'sec_symbol_unresolved': FailureKind.UPSTREAM_SCHEMA,
        'sec_company_scope_required': FailureKind.BLOCKED_BY_POLICY,
        'material_cursor_stale': FailureKind.UPSTREAM_SCHEMA,
        'audio_source_changed': FailureKind.UPSTREAM_SCHEMA,
        'audio_dependency_missing': FailureKind.UNIMPLEMENTED,
        'audio_format_invalid': FailureKind.UPSTREAM_SCHEMA,
        'audio_provider_error': FailureKind.UPSTREAM_SCHEMA,
        'audio_incomplete': FailureKind.NETWORK,
        'audio_no_speech': FailureKind.NONE,
        'audio_time_budget_insufficient': FailureKind.BUDGET_EXHAUSTED,
        'audio_cache_invalid': FailureKind.BLOCKED_BY_POLICY,
        'audio_cache_unavailable': FailureKind.BLOCKED_BY_POLICY,
        'audio_cache_miss': FailureKind.NONE,
        'audio_media_too_large': FailureKind.BUDGET_EXHAUSTED,
        'audio_access_unavailable': FailureKind.BLOCKED_BY_POLICY,
        'audio_window_out_of_range': FailureKind.UNIMPLEMENTED,

        "video_dependency_missing": FailureKind.UNIMPLEMENTED,
        "video_captions_unavailable": FailureKind.UNIMPLEMENTED,
        "video_captions_login_required": FailureKind.NO_CREDENTIAL,
        "video_caption_url_unavailable": FailureKind.UPSTREAM_SCHEMA,
        "video_caption_timing_mismatch": FailureKind.UPSTREAM_SCHEMA,
        "video_caption_language_unavailable": FailureKind.UNIMPLEMENTED,
        "wisburg_call_budget_exhausted": FailureKind.BUDGET_EXHAUSTED,
        "browser_dependency_missing": FailureKind.UNIMPLEMENTED,
        "browser_version_unsupported": FailureKind.UNIMPLEMENTED,
        "browser_unavailable": FailureKind.UNIMPLEMENTED,
        "browser_failed": FailureKind.UPSTREAM_SCHEMA,
        "browser_robots_denied": FailureKind.BLOCKED_BY_POLICY,
        "browser_budget_exhausted": FailureKind.BUDGET_EXHAUSTED,
        "web_content_loading": FailureKind.UPSTREAM_SCHEMA,
        "web_content_challenge": FailureKind.BLOCKED_BY_POLICY,
        "web_content_image_only": FailureKind.UNIMPLEMENTED,
        "web_content_extractor_error": FailureKind.UPSTREAM_SCHEMA,
        "web_content_empty": FailureKind.UPSTREAM_SCHEMA,
        "ima_upstream_rejected": FailureKind.BLOCKED_BY_POLICY,
        "ima_original_unavailable": FailureKind.UNIMPLEMENTED,
        "wechat_account_unresolved": FailureKind.UPSTREAM_SCHEMA,
        "wechat_account_mismatch": FailureKind.UPSTREAM_SCHEMA,
        "wechat_article_mismatch": FailureKind.UPSTREAM_SCHEMA,
        "wechat_origin_unavailable": FailureKind.BLOCKED_BY_POLICY,
        "entitlement_denied": FailureKind.BLOCKED_BY_POLICY,
        "authentication_failed": FailureKind.BLOCKED_BY_POLICY,
        "fmp_request_budget_exceeded": FailureKind.BUDGET_EXHAUSTED,
        "tushare_call_budget_exhausted": FailureKind.BUDGET_EXHAUSTED,
        "tushare_response_too_large": FailureKind.UPSTREAM_SCHEMA,
        "tushare_response_too_many_rows": FailureKind.UPSTREAM_SCHEMA,
        "no_credential": FailureKind.NO_CREDENTIAL,
        "quota": FailureKind.QUOTA,
        "rate_limit": FailureKind.RATE_LIMIT,
        "network": FailureKind.NETWORK,
        "timeout": FailureKind.TIMEOUT,
        "unsupported": FailureKind.UNIMPLEMENTED,
        "upstream_schema": FailureKind.UPSTREAM_SCHEMA,
        "response_too_large": FailureKind.UPSTREAM_SCHEMA,
        "blocked_url": FailureKind.BLOCKED_BY_POLICY,
        "web_redirect_limit": FailureKind.BLOCKED_BY_POLICY,
        "web_content_unsupported": FailureKind.UNIMPLEMENTED,
        "no_extracted_text": FailureKind.UPSTREAM_SCHEMA,
        "dependency_missing": FailureKind.UNIMPLEMENTED,
        "tls_error": FailureKind.BLOCKED_BY_POLICY,
        "tls_not_supported": FailureKind.BLOCKED_BY_POLICY,
        "units_not_configured": FailureKind.BLOCKED_BY_POLICY,
        "invalid_cursor": FailureKind.BLOCKED_BY_POLICY,
        "source_disabled": FailureKind.UNIMPLEMENTED,
        "source_config_error": FailureKind.BLOCKED_BY_POLICY,
        "not_found": FailureKind.NONE,
        "historical_intraday_source_not_configured": FailureKind.UNIMPLEMENTED,
        "current_day_only": FailureKind.UNIMPLEMENTED,
        "stale_intraday_data": FailureKind.UPSTREAM_SCHEMA,
    }

    def __init__(self, code: str):
        if code not in self.KINDS:
            raise ValueError("Unknown adapter error code")
        super().__init__(code)
        self.code = code
        self.failure_kind = self.KINDS[code]


_DATASETS = {
    "securities": DatasetDefinition(
        "securities", "Security directory; market and exchange identify the listing venue.",
        (
            FieldDefinition("symbol", "string", "Source-normalized ticker; consult market and exchange for venue"),
            FieldDefinition("name", "string", "Security display name"),
            FieldDefinition("market", "string", "Market, such as A_SHARE, HK or US"),
            FieldDefinition("exchange", "string", "Listing exchange"),
            FieldDefinition("currency", "string", "Trading currency, ISO currency code"),
            FieldDefinition("available_at", "datetime", "Public availability time, if known", nullable=True),
        ), ("symbol",),
    ),
    "prices_daily": DatasetDefinition(
        "prices_daily", "Daily OHLCV; currency is per record, adjustment is explicit in the request.",
        (
            FieldDefinition("symbol", "string", "Source-normalized code including listing venue"),
            FieldDefinition("trade_date", "date", "Trading date in the exchange calendar"),
            FieldDefinition("open", "number", "Opening price", "quote_currency", True),
            FieldDefinition("high", "number", "Highest price", "quote_currency", True),
            FieldDefinition("low", "number", "Lowest price", "quote_currency", True),
            FieldDefinition("close", "number", "Closing price", "quote_currency", True),
            FieldDefinition("volume", "number", "Volume in shares, not lots", "shares", True),
            FieldDefinition("amount", "number", "Turnover", "quote_currency", True),
            FieldDefinition("currency", "string", "Trading currency, ISO currency code"),
            FieldDefinition("available_at", "datetime", "Public availability time, if known", nullable=True),
        ), ("symbol", "trade_date"), date_field="trade_date",
    ),
    "prices_intraday": DatasetDefinition(
        "prices_intraday", "Recent A-share bars; Asia/Shanghai timestamps, raw prices, no guaranteed full-session coverage.",
        (
            FieldDefinition("symbol", "string", "Stock code including listing venue"),
            FieldDefinition("bar_time", "datetime", "Source bar timestamp with Asia/Shanghai offset; not a tick or publication time"),
            FieldDefinition("trade_date", "date", "A-share local trading date"),
            FieldDefinition("open", "number", "Opening price; unknown/zero source price becomes null", "CNY_per_share", True),
            FieldDefinition("high", "number", "Highest price", "CNY_per_share", True),
            FieldDefinition("low", "number", "Lowest price", "CNY_per_share", True),
            FieldDefinition("close", "number", "Closing price", "CNY_per_share", True),
            FieldDefinition("volume", "number", "Volume converted from source lots to shares", "shares", True),
            FieldDefinition("amount", "number", "Turnover", "CNY", True),
            FieldDefinition("currency", "string", "CNY"),
        ), ("symbol", "bar_time"), date_field="trade_date",
    ),
    "futures_intraday": DatasetDefinition(
        "futures_intraday", "Recent concrete-contract bars; date bounds use Asia/Shanghai calendar dates, NOT exchange trading days.",
        (
            FieldDefinition("symbol", "string", "Sina native concrete contract code; continuous contracts are excluded"),
            FieldDefinition("bar_time", "datetime", "Source timestamp in Asia/Shanghai; bar closure is unverified"),
            FieldDefinition("calendar_date", "date", "Natural date of timestamp, including night sessions"),
            FieldDefinition("trade_date", "date", "Date resolved from published open dates for an observed bar; null when unresolved", nullable=True),
            FieldDefinition("trade_date_source", "string", "Calendar provider and resolution method, distinct from quote provider", nullable=True),
            FieldDefinition("open", "number", "Opening quotation in the contract's native quote unit", "native_contract_quote", True),
            FieldDefinition("high", "number", "Highest quotation", "native_contract_quote", True),
            FieldDefinition("low", "number", "Lowest quotation", "native_contract_quote", True),
            FieldDefinition("close", "number", "Closing quotation", "native_contract_quote", True),
            FieldDefinition("source_volume", "number", "Source count; unit and counting convention unverified", "source_native_count_unverified", True),
            FieldDefinition("source_open_interest", "number", "Source holding count; counting convention unverified", "source_native_count_unverified", True),
            FieldDefinition("currency", "string", "Settlement currency; does not imply a CNY price per contract"),
        ), ("symbol", "bar_time"), date_field="calendar_date",
    ),
}


class DataAdapter(Protocol):
    """Adapters normalize source fields and preserve all row/page diagnostics.

    query_data is one service operation. Implementations must propagate context's
    remaining timeout and account scope; credentials stay private to their client.
    """
    name: str
    capabilities: tuple[DataCapability, ...]

    def query_data(self, request: DataRequest, *, context: RequestContext) -> DataPage:
        ...


from .contracts.market import MARKET_DATASETS
_DATASETS.update(MARKET_DATASETS)
from .contracts.overseas import OVERSEAS_DATASETS
_DATASETS.update(OVERSEAS_DATASETS)
from .contracts.expanded_data import EXPANDED_DATASETS
_DATASETS.update(EXPANDED_DATASETS)


class DataRegistry:
    def __init__(self, *, use_source_policy: bool = False):
        if type(use_source_policy) is not bool:
            raise ValueError("use_source_policy must be boolean")
        self.use_source_policy = use_source_policy
        self._entries: dict[tuple[str, str, str], tuple[DataCapability, DataAdapter]] = {}
        self.diagnostics: list[Diagnostic] = []

    def register(self, adapter: DataAdapter) -> None:
        """Register atomically. Discovery metadata is not an online health check."""
        pending = {}
        if not callable(getattr(adapter, "query_data", None)) or not adapter.capabilities:
            raise ValueError("Data adapters must declare capabilities and query_data")
        for capability in adapter.capabilities:
            if not isinstance(capability, DataCapability) or capability.provider != adapter.name:
                raise ValueError("Adapter name and capability provider must match")
            definition = _DATASETS.get(capability.dataset)
            if definition is None:
                raise ValueError("Dataset has not been defined")
            known = {item.name for item in definition.fields}
            if not set(capability.fields) <= known or not set(definition.primary_key) <= set(capability.fields):
                raise ValueError("Capability fields must be known and include the primary key")
            if not capability.markets or not capability.frequencies or not capability.adjustments or not capability.value_kinds:
                raise ValueError("Capability coverage must not be empty")
            key = (capability.provider, capability.dataset, capability.account_scope)
            if key in self._entries or key in pending:
                raise ValueError("Provider/dataset/account scope is already registered")
            pending[key] = (capability, adapter)
        self._entries.update(pending)

    def entries(self, *, account_scope: str = "default") -> tuple[tuple[DataCapability, DataAdapter], ...]:
        """Return only the local account scope, in deterministic provider order."""
        return tuple(value for key, value in sorted(self._entries.items()) if key[2] == account_scope)


def build_data_registry(*, env_file=None) -> DataRegistry:
    """Register explicitly enabled data sources without connecting or importing drivers."""
    from .infrastructure.credentials import mysql_profile, read_credentials, SourceConfigError
    registry = DataRegistry(use_source_policy=True)
    wind_calendar_profile = None
    try:
        values = read_credentials(env_file)
    except SourceConfigError as exc:
        registry.diagnostics.append(Diagnostic(exc.code, "configure",
                                               failure_kind=FailureKind.BLOCKED_BY_POLICY))
        return registry
    for provider in ("wind_mysql", "jydb"):
        try:
            profile = mysql_profile(provider, values=values)
            if profile:
                if provider == "wind_mysql":
                    wind_calendar_profile = profile
                    from .adapters.wind_mysql import WindMySQLAdapter
                    registry.register(WindMySQLAdapter(profile))
                else:
                    from .adapters.jydb_market import JYDBMarketAdapter
                    registry.register(JYDBMarketAdapter(profile))
                from .adapters.financial_statements import FinancialStatementsAdapter
                registry.register(FinancialStatementsAdapter(profile))
                from .adapters.domestic_derivatives import DomesticDerivativesAdapter
                registry.register(DomesticDerivativesAdapter(profile))
                from .adapters.funds import FundAdapter
                registry.register(FundAdapter(profile))
        except SourceConfigError as exc:
            registry.diagnostics.append(Diagnostic(exc.code, "configure", provider=provider,
                                                   failure_kind=FailureKind.BLOCKED_BY_POLICY))
    try:
        from .infrastructure.credentials import fmp_profile
        profile = fmp_profile(values=values)
        if profile:
            from .adapters.fmp import FMPAdapter
            registry.register(FMPAdapter(profile))
    except SourceConfigError as exc:
        registry.diagnostics.append(Diagnostic(exc.code, "configure", provider="fmp",
                                               failure_kind=FailureKind.BLOCKED_BY_POLICY))
    try:
        from .infrastructure.fiona import fiona_profile
        from .adapters.fiona import FionaAdapter
        profile = fiona_profile(values=values)
        if profile: registry.register(FionaAdapter(profile))
    except SourceConfigError as exc:
        registry.diagnostics.append(Diagnostic(exc.code, 'configure', 'fiona', failure_kind=FailureKind.BLOCKED_BY_POLICY))
    enabled_macro = values.get('GLOBAL_MACRO_ENABLED', 'false').lower()
    if enabled_macro == 'true':
        from .adapters.global_macro import GlobalMacroAdapter
        registry.register(GlobalMacroAdapter())
    elif enabled_macro != 'false':
        registry.diagnostics.append(Diagnostic('source_config_error', 'configure', 'global_macro', failure_kind=FailureKind.BLOCKED_BY_POLICY))
    enabled = values.get("AKSHARE_ENABLED", "false").lower()
    if enabled == "true":
        from .adapters.akshare_intraday import AKShareIntradayAdapter
        from .adapters.akshare_futures import AKShareFuturesAdapter
        backend = values.get("AKSHARE_STOCK_BACKEND", "eastmoney")
        if backend in {"eastmoney", "sina"}:
            registry.register(AKShareIntradayAdapter(backend=backend))
        else:
            registry.diagnostics.append(Diagnostic("source_config_error", "configure", provider="akshare",
                                                   failure_kind=FailureKind.BLOCKED_BY_POLICY))
        registry.register(AKShareFuturesAdapter(calendar_profile=wind_calendar_profile))
        from .adapters.akshare_options import AKShareOptionsAdapter
        registry.register(AKShareOptionsAdapter())
    elif enabled != "false":
        registry.diagnostics.append(Diagnostic("source_config_error", "configure", provider="akshare",
                                               failure_kind=FailureKind.BLOCKED_BY_POLICY))
    return registry


def list_capabilities(*, registry: DataRegistry = None, account_scope: str = "default", material_registry=None) -> dict:
    """Read local declarations without authenticating, probing or importing data SDKs."""
    registry = registry if registry is not None else build_data_registry()
    from .source_policy import source_policy
    from .material_registry import list_material_capabilities
    from .infrastructure.web_browser import _browser_status
    from .infrastructure.web_toolkit import WEB_READ_MODES, web_toolkit_status
    from .adapters.global_macro import _catalog as macro_catalog
    return {
        "schema_version": "1.0",
        "operation": "query_data",
        "macro_series_catalog": {"curated_not_exhaustive": True, "series": list(macro_catalog().values()), "symbol_patterns": ["FRED.<series_id>", "WB.<ISO3>.<series_id>", "ECB.EXR.D.<CCY>.EUR.SP00.A"]},
        "datasets": list(_DATASETS),
        "capabilities": [cap.to_dict() for cap, _ in registry.entries(account_scope=account_scope)],
        "verification_basis": "registration_metadata_not_live_probe",
        "materials": list_material_capabilities(registry=material_registry, account_scope=account_scope),
        "source_policy": source_policy() if registry.use_source_policy else None,
        "web_reading": {"modes": list(WEB_READ_MODES), "default": "auto", "browser": _browser_status(),
                        "optional_readers": web_toolkit_status(),
                        "auto_trigger": "observed_loading_gap", "source_text_trust": "untrusted"},
        "diagnostics": [item.to_dict() for item in registry.diagnostics],
    }


def describe_dataset(dataset: str, *, registry: DataRegistry = None, account_scope: str = "default") -> dict:
    """Describe the schema separately from provider implementation and authorization."""
    if not isinstance(dataset, str):
        raise ValueError("dataset must be a string")
    definition = _DATASETS.get(dataset)
    catalog = list_capabilities(registry=registry, account_scope=account_scope)
    return {
        "schema_version": "1.0",
        "status": "ok" if definition else "unavailable",
        "definition": definition.to_dict() if definition else None,
        "capabilities": [cap for cap in catalog["capabilities"] if cap["dataset"] == dataset],
        "verification_basis": catalog["verification_basis"],
        "source_policy": catalog["source_policy"],
        "diagnostics": catalog["diagnostics"] + ([] if definition else [{"code": "unknown_dataset", "operation": "describe_dataset"}]),
    }
