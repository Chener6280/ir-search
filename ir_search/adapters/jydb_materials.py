"""Bridge the existing JYDB announcement reader into bounded material search."""
from datetime import date, datetime
import re

from .jydb import JYDBAnnouncementAdapter
from ir_search.contracts import AdapterMode, AnnouncementRequest, Diagnostic, Provenance
from ir_search.contracts.materials import MaterialCandidate, MaterialCapability, MaterialKind, MaterialSearchPage, TextScope
from ir_search.context import RequestStopped
from ir_search.models import EvidenceType, SourceAuthority, SourceTier
from ir_search.registry import DataAdapterError


class JYDBMaterialAdapter:
    name = "jydb"

    def __init__(self, profile, *, client=None):
        if getattr(profile, "provider", None) != self.name:
            raise ValueError("JYDB profile required")
        self._client = client if client is not None else JYDBAnnouncementAdapter(profile)
        self.capability = MaterialCapability(self.name, "database", (MaterialKind.ANNOUNCEMENT,),
            requires_symbols=True, max_symbols=1, coverage_notes=(
                "One A-share issuer; bounded newest LC_Announcement page, then optional text reads",
                "Publication window is separate from requested business period; business period remains unknown",
                "Vendor transcription, no original web URL or independent original-file verification",))

    def search_materials(self, request, *, context):
        """Read a bounded company/date page; central service matches research terms."""
        if (len(request.symbols) != 1 or not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", request.symbols[0])
                or not request.published_start or request.published_end == date.max
                or context.account_scope != self.capability.account_scope):
            raise DataAdapterError("unsupported")
        listing = self._client.search_announcements(AnnouncementRequest(
            request.symbols, request.published_start, request.published_end, limit=request.candidates_per_source), context=context)
        fetched = datetime.fromisoformat(listing["fetched_at"])
        diagnostics = [Diagnostic("bounded_announcement_page", "search_materials", self.name, adapter_mode=AdapterMode.LIVE),
                       Diagnostic("publication_time_precision_unverified", "search_materials", self.name, adapter_mode=AdapterMode.LIVE)]
        candidates = []
        for index, item in enumerate(listing["items"]):
            context.check_active()
            published = datetime.fromisoformat(item["published_at"]).date()
            text, scope = "", TextScope.METADATA
            warnings = ["vendor_text_not_original_file", "publication_time_precision_unverified", "business_period_unknown"]
            provenance = Provenance(self.name, "unknown", fetched, authority=SourceAuthority.DATA_VENDOR,
                source_tier=SourceTier.COMPANY, evidence_type=EvidenceType.ANNOUNCEMENT, adapter_mode=AdapterMode.LIVE)
            if index < request.text_reads_per_source:
                try:
                    document = self._client.fetch_document(item["source_ref"], max_chars=request.max_chars, context=context)
                    if document.url != item["source_ref"] or document.source != self.name:
                        raise DataAdapterError("upstream_schema")
                    text, scope = document.text, TextScope.EXTRACTED_TEXT
                    warnings.extend(w for w in document.warnings if w in {"text_truncated", "publisher_unknown", "source_uri_not_web_url"})
                    provenance = Provenance(self.name, document.extra.get("publisher") or "unknown", document.fetched_at,
                        authority=SourceAuthority.DATA_VENDOR, source_tier=SourceTier.COMPANY,
                        evidence_type=EvidenceType.ANNOUNCEMENT, adapter_mode=AdapterMode.LIVE)
                except DataAdapterError as exc:
                    diagnostics.append(Diagnostic(exc.code, "search_materials", self.name, failure_kind=exc.failure_kind,
                                                  adapter_mode=AdapterMode.LIVE))
                    warnings.append("text_fetch_failed")
                except RequestStopped as exc:
                    diagnostics.append(Diagnostic(exc.code, "search_materials", self.name, failure_kind=exc.failure_kind,
                                                  adapter_mode=AdapterMode.LIVE))
                    break
            else:
                warnings.append("text_not_read_within_budget")
            candidates.append(MaterialCandidate(item["source_ref"], item["title"], MaterialKind.ANNOUNCEMENT,
                "database", provenance, text=text, text_scope=scope, symbols=request.symbols,
                published_on=published, warnings=tuple(dict.fromkeys(warnings))))
        if len(listing["items"]) > request.text_reads_per_source:
            diagnostics.append(Diagnostic("text_read_budget_exhausted", "search_materials", self.name, adapter_mode=AdapterMode.LIVE))
        # A first-page scan cannot guarantee complete topic or business-period coverage.
        return MaterialSearchPage(candidates, len(listing["items"]), complete=False, diagnostics=diagnostics)
