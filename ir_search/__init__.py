from .context import RequestContext
from .contracts import (
    AccessStatus, AdapterMode, AdvisoryResult, AnnouncementRequest, DataCapability, DataPage, DataRequest,
    DataResult, Diagnostic, MaterialBundle, MaterialRequest, Provenance, Status, ValueKind,
)
from .registry import DataAdapterError, DataRegistry, build_data_registry, describe_dataset, list_capabilities
from .services.data import get_data
from .services.retrieval import retrieve
from .services.material_archive import export_material
from .services.announcements import search_announcements
from .contracts.materials import (
    MaterialSearchRequest, MaterialSearchResult, MaterialSearchPage, MaterialCandidate,
    MaterialCapability, MaterialKind, MaterialSourceScan, MaterialAttachment, MaterialSection, TextScope, WebSearchFallback,
)
from .material_registry import MaterialRegistry, build_material_registry, list_material_capabilities
from .services.material_search import search_materials
from .services.material_runs import next_material_request, record_material_run
from .services.source_diagnostics import diagnose_sources
from .institutions import list_institutions
from .infrastructure.credentials import source_configuration_status
from .source_policy import source_policy
from .models import (
    Entity,
    EntityType,
    CoverageStatus,
    EvidenceType,
    FallbackPolicy,
    FailureKind,
    Hit,
    Intent,
    Lang,
    Query,
    ResultKind,
    SearchResult,
    SourceAuthority,
    SourceStatus,
    SourceTier,
    TimeWindow,
)

__all__ = [
    "Entity",
    "EntityType",
    "CoverageStatus",
    "EvidenceType",
    "FallbackPolicy",
    "FailureKind",
    "Hit",
    "Intent",
    "Lang",
    "Query",
    "ResultKind",
    "SearchResult",
    "SourceAuthority",
    "SourceStatus",
    "SourceTier",
    "TimeWindow",
    "build_registry",
    "search",
    "AccessStatus", "AdapterMode", "AdvisoryResult", "DataAdapterError", "DataCapability",
    "DataPage", "DataRegistry", "DataRequest", "DataResult", "Diagnostic",
    "MaterialBundle", "MaterialRequest", "Provenance", "RequestContext", "Status", "ValueKind",
    "build_data_registry", "describe_dataset", "get_data", "list_capabilities", "retrieve", "export_material",
    "AnnouncementRequest", "search_announcements", "source_configuration_status",
    "source_policy",
    "MaterialSearchRequest", "MaterialSearchResult", "MaterialSearchPage", "MaterialCandidate",
    "MaterialCapability", "MaterialKind", "MaterialSourceScan", "MaterialAttachment", "MaterialSection", "TextScope", "MaterialRegistry",
    "build_material_registry", "list_material_capabilities", "search_materials", "list_institutions",
    "diagnose_sources", "next_material_request", "record_material_run",
    "WebSearchFallback",
]


def build_registry(live=None):
    """Load legacy search adapters only when the legacy registry is requested."""
    from .kernel import build_registry as implementation
    return implementation(live=live)


def search(x, registry=None, cache=None, logger=None):
    """Compatible search facade; the data SDK does not import legacy source clients."""
    from .kernel import search as implementation
    return implementation(x, registry=registry, cache=cache, logger=logger)
