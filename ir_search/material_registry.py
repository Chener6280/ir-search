"""Material discovery is independent of numeric datasets and legacy search."""
from __future__ import annotations

from typing import Protocol

from .contracts import Diagnostic
from .contracts.materials import MaterialCapability, MaterialSearchPage, MaterialSearchRequest
from .context import RequestContext
from .models import FailureKind

# Intent catalog, not adapters or promises about external account access.
MATERIAL_SOURCE_INTENT = {
    "hkex": "official_filings",
    "company_ir": "company_ir",
    "rss": "feed",
    "xhs": "community",
    "sec": "official_filings",
    "jydb": "database",
    "tushare_corpus": "corpus",
    "ima": "knowledge_base",
    "zsxq": "community",
    "web": "web",
    "wechat": "wechat",
    "alphapai": "research_platform",
    "gangtise": "research_platform",
    "wisburg": "research_platform",
    "xueqiu": "community",
    "eastmoney": "community",
    "video": "video",
    "xiaoyuzhou": "audio",
}


class MaterialAdapter(Protocol):
    name: str
    capability: MaterialCapability

    def search_materials(self, request: MaterialSearchRequest, *, context: RequestContext) -> MaterialSearchPage:
        ...


class MaterialRegistry:
    def __init__(self):
        self._entries = {}
        self.diagnostics = []

    def register(self, adapter: MaterialAdapter) -> None:
        """Register a declared material capability without network access."""
        capability = getattr(adapter, "capability", None)
        if (not isinstance(capability, MaterialCapability) or capability.provider != adapter.name
                or not callable(getattr(adapter, "search_materials", None))):
            raise ValueError("Invalid material adapter")
        key = (capability.provider, capability.account_scope)
        if key in self._entries:
            raise ValueError("Material provider/account already registered")
        self._entries[key] = adapter

    def entries(self, *, account_scope="default") -> tuple:
        """Return deterministic, account-isolated provider entries."""
        return tuple(adapter for (provider, scope), adapter in sorted(self._entries.items()) if scope == account_scope)


def build_material_registry(*, env_file=None) -> MaterialRegistry:
    """Register implemented sources only; a filled token is not an adapter."""
    from .infrastructure.credentials import SourceConfigError, mysql_profile, read_credentials, tushare_corpus_profile, web_material_profile, zsxq_profile, wechat_profile
    registry = MaterialRegistry()
    try:
        values = read_credentials(env_file)
    except SourceConfigError as exc:
        registry.diagnostics.append(Diagnostic(exc.code, "configure", failure_kind=FailureKind.BLOCKED_BY_POLICY))
        return registry
    try:
        profile = mysql_profile("jydb", values=values, datasets=())
        if profile:
            from .adapters.jydb_materials import JYDBMaterialAdapter
            registry.register(JYDBMaterialAdapter(profile))
    except SourceConfigError as exc:
        registry.diagnostics.append(Diagnostic(exc.code, "configure", "jydb", failure_kind=FailureKind.BLOCKED_BY_POLICY))
    try:
        profile = tushare_corpus_profile(values=values)
        if profile:
            from .adapters.tushare_corpus import TushareCorpusAdapter
            registry.register(TushareCorpusAdapter(profile))
    except SourceConfigError as exc:
        registry.diagnostics.append(Diagnostic(exc.code, "configure", "tushare_corpus", failure_kind=FailureKind.BLOCKED_BY_POLICY))
    try:
        profile = web_material_profile(values=values)
        if profile:
            from .adapters.web_materials import WebMaterialAdapter
            registry.register(WebMaterialAdapter(profile))
    except SourceConfigError as exc:
        registry.diagnostics.append(Diagnostic(exc.code, "configure", "web", failure_kind=FailureKind.BLOCKED_BY_POLICY))
    try:
        profile = zsxq_profile(values=values)
        if profile:
            from .adapters.zsxq_materials import ZsxqMaterialAdapter
            registry.register(ZsxqMaterialAdapter(profile))
    except SourceConfigError as exc:
        registry.diagnostics.append(Diagnostic(exc.code, "configure", "zsxq", failure_kind=FailureKind.BLOCKED_BY_POLICY))
    try:
        profile = wechat_profile(values=values, env_file=env_file)
        if profile:
            from .adapters.wechat_materials import WechatMaterialAdapter
            registry.register(WechatMaterialAdapter(profile))
    except SourceConfigError as exc:
        registry.diagnostics.append(Diagnostic(exc.code, "configure", "wechat", failure_kind=FailureKind.BLOCKED_BY_POLICY))
    try:
        from .infrastructure.credentials import ima_profile
        profile = ima_profile(values=values)
        if profile:
            from .adapters.ima_materials import IMAMaterialAdapter
            registry.register(IMAMaterialAdapter(profile))
    except SourceConfigError as exc:
        registry.diagnostics.append(Diagnostic(exc.code, "configure", "ima", failure_kind=FailureKind.BLOCKED_BY_POLICY))
    try:
        from .infrastructure.credentials import wisburg_profile
        profile = wisburg_profile(values=values)
        if profile:
            from .adapters.wisburg_materials import WisburgMaterialAdapter
            registry.register(WisburgMaterialAdapter(profile))
    except SourceConfigError as exc:
        registry.diagnostics.append(Diagnostic(exc.code, "configure", "wisburg", failure_kind=FailureKind.BLOCKED_BY_POLICY))
    from .adapters.platform_materials import platform_material_profile, XueqiuMaterialAdapter, EastmoneyMaterialAdapter, VideoMaterialAdapter, XiaoyuzhouMaterialAdapter
    for cls in (XueqiuMaterialAdapter, EastmoneyMaterialAdapter, VideoMaterialAdapter, XiaoyuzhouMaterialAdapter):
        try:
            profile = platform_material_profile(cls.name, values=values)
            if profile:
                registry.register(cls(profile))
        except SourceConfigError as exc:
            registry.diagnostics.append(Diagnostic(exc.code, "configure", cls.name, failure_kind=FailureKind.BLOCKED_BY_POLICY))
    try:
        from .infrastructure.sec import sec_profile
        from .adapters.sec_materials import SECMaterialAdapter
        profile = sec_profile(values=values)
        if profile:
            registry.register(SECMaterialAdapter(profile))
    except SourceConfigError as exc:
        registry.diagnostics.append(Diagnostic(exc.code, 'configure', 'sec', failure_kind=FailureKind.BLOCKED_BY_POLICY))
    try:
        from .infrastructure.alphapai import alphapai_profile
        from .adapters.alphapai_materials import AlphapaiMaterialAdapter
        profile = alphapai_profile(values=values)
        if profile:
            registry.register(AlphapaiMaterialAdapter(profile))
    except SourceConfigError as exc:
        registry.diagnostics.append(Diagnostic(exc.code, 'configure', 'alphapai', failure_kind=FailureKind.BLOCKED_BY_POLICY))
    try:
        from .infrastructure.gangtise import gangtise_profile
        from .adapters.gangtise_materials import GangtiseMaterialAdapter
        profile = gangtise_profile(values=values)
        if profile:
            registry.register(GangtiseMaterialAdapter(profile))
    except SourceConfigError as exc:
        registry.diagnostics.append(Diagnostic(exc.code, 'configure', 'gangtise', failure_kind=FailureKind.BLOCKED_BY_POLICY))
    try:
        from .infrastructure.xhs import xhs_profile
        from .adapters.xhs_materials import XhsMaterialAdapter
        profile = xhs_profile(values=values, env_file=env_file)
        if profile:
            registry.register(XhsMaterialAdapter(profile))
    except SourceConfigError as exc:
        registry.diagnostics.append(Diagnostic(exc.code, 'configure', 'xhs', failure_kind=FailureKind.BLOCKED_BY_POLICY))
    try:
        from .infrastructure.rss import rss_profile
        from .adapters.rss_materials import RSSMaterialAdapter
        profile = rss_profile(values=values)
        if profile:
            registry.register(RSSMaterialAdapter(profile))
    except SourceConfigError as exc:
        registry.diagnostics.append(Diagnostic(exc.code, 'configure', 'rss', failure_kind=FailureKind.BLOCKED_BY_POLICY))
    from .adapters.official_materials import HKEXMaterialAdapter, CompanyIRMaterialAdapter
    for cls, key in ((HKEXMaterialAdapter, 'HKEX_ENABLED'), (CompanyIRMaterialAdapter, 'COMPANY_IR_ENABLED')):
        enabled = values.get(key, 'false').lower()
        if enabled == 'true': registry.register(cls())
        elif enabled != 'false':
            registry.diagnostics.append(Diagnostic('source_config_error', 'configure', cls.name, failure_kind=FailureKind.BLOCKED_BY_POLICY))
    return registry


def list_material_capabilities(*, registry=None, account_scope="default") -> dict:
    """Expose actual registrations and separate unregistered source intentions."""
    from .institutions import list_institutions
    from .infrastructure.official_materials import _issuers
    registry = registry if registry is not None else build_material_registry()
    entries = registry.entries(account_scope=account_scope)
    registered = {adapter.name for adapter in entries}
    return {"schema_version": "1.0", "operation": "search_materials",
            "verification_basis": "registration_metadata_not_live_probe",
            "institution_catalog": list_institutions(),
            "company_ir_catalog": {"curated_not_exhaustive": True, "issuers": [{"symbol": r.symbol, "name": r.name, "domains": list(r.domains), "directory_urls": list(r.directory_urls)} for r in _issuers()]},
            "capabilities": [adapter.capability.to_dict() for adapter in entries],
            "unregistered_sources": [{"provider": name, "channel": channel, "state": "not_registered"}
                                     for name, channel in MATERIAL_SOURCE_INTENT.items() if name not in registered],
            "diagnostics": [d.to_dict() for d in registry.diagnostics]}
