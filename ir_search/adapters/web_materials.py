"""Discover public URLs and read bounded original text for the material service."""
from __future__ import annotations

from datetime import datetime
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor

from ir_search.context import RequestStopped
import re
from urllib.parse import urlsplit

from ir_search.contracts import AdapterMode, Diagnostic, Provenance
from ir_search.contracts.materials import (MaterialCandidate, MaterialCapability, MaterialKind, MaterialAttachment,
    MaterialSearchPage, MaterialSourceScan, TextScope, WebSearchFallback)
from ir_search.infrastructure.credentials import WebMaterialProfile
from ir_search.infrastructure.public_web import _url, _allowed_domain
from ir_search.infrastructure.web_documents import read_web_document, _VALID
from ir_search.infrastructure.web_search import AnySearchWebClient, BochaWebClient, ExaWebClient
from ir_search.infrastructure.web_routing import route_web_search
from ir_search.infrastructure.web_material_plan import _web_plan, _web_query, _effective_domains
from ir_search.institutions import _institution_for_url
from ir_search.models import EvidenceType, SourceAuthority, SourceTier
from ir_search.registry import DataAdapterError

_KINDS = (MaterialKind.WEB_PAGE, MaterialKind.NEWS, MaterialKind.POLICY)
_MEDIA = ("reuters.com", "xinhuanet.com", "news.cn", "people.com.cn", "cnstock.com", "stcn.com", "yicai.com", "caixin.com")


def _diag(code, failure=None):
    args = {"failure_kind": failure} if failure is not None else {}
    return Diagnostic(code, "search_materials", "web", adapter_mode=AdapterMode.LIVE, **args)


def _classification(host, title, has_original):
    institution = _institution_for_url("https://" + host)
    government = host.endswith(".gov.cn") or host == "gov.cn" or bool(institution and institution.category == "regulator")
    policy = government and any(word in title for word in ("通知", "办法", "意见", "行动方案", "条例", "规划纲要"))
    media = any(host == d or host.endswith("." + d) for d in _MEDIA)
    kind = MaterialKind.POLICY if policy else MaterialKind.NEWS if media else MaterialKind.WEB_PAGE
    evidence = EvidenceType.POLICY_DOC if policy else EvidenceType.NEWS if media else EvidenceType.UNKNOWN
    tier = (SourceTier.REGULATOR if government else SourceTier.COMPANY if institution else SourceTier.MEDIA) if has_original else SourceTier.MEDIA
    return kind, evidence, tier


def _publication(value):
    if value in (None, ""):
        return None, None
    if isinstance(value, str):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?)?", value):
            raise ValueError("Invalid publication date")
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise ValueError("Invalid publication date")
    return value.date(), value if value.utcoffset() is not None else None


def _field(row, key, *, required=False):
    value = row.get(key)
    if value is None:
        value = ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError("Invalid discovery record")
    return value.strip()


class WebMaterialAdapter:
    name = "web"

    def __init__(self, profile: WebMaterialProfile, *, client=None, bocha_client=None, exa_client=None, reader=None):
        if not isinstance(profile, WebMaterialProfile):
            raise ValueError("WebMaterialProfile required")
        self._profile = profile
        self._client = client if client is not None else AnySearchWebClient(profile)
        self._bocha_client = bocha_client if bocha_client is not None else BochaWebClient(profile)
        self._exa_client = exa_client if exa_client is not None else ExaWebClient(profile)
        self._reader = reader or read_web_document
        self.capability = MaterialCapability(self.name, "web", _KINDS, supports_publication_filter=False,
            search_basis="remote_web_discovery_then_local_matching", coverage_notes=(
                "Regional discovery: CN Bocha, configured overseas Exa/AnySearch; explicit web_region overrides inferred region",
                "Exhausted search quota produces a pending caller-native Web Search handoff; no anonymous retry",
                "Fixed-provider configurations retain their selection; cross-region queries share a total 10-URL budget",
                "Publication dates are filtered locally; requested dates in query text are hints, not upstream guarantees",
                "Search snippets remain separate from original text; unknown dates and document types are explicit",
                "web_page is unclassified web content; policy/news classification is conservative and rule-based",
                "No automatic pagination, browser login, OCR, generated summaries or legacy research orchestration",))

    def _prefetch(self, rows, request, context, domains):
        # Select in discovery order before starting work. Invalid/duplicate URLs
        # never use a body-read slot. Workers return outcomes, not shared mutations.
        urls, seen = [], set()
        for row in rows:
            try:
                url = _field(row, 'url', required=True)
                _, host, _ = _url(url)
                _field(row, 'title', required=True)
                _field(row, 'snippet')
                if not _allowed_domain(host, domains) or url in seen:
                    continue
                seen.add(url)
                if len(urls) < request.text_reads_per_source:
                    urls.append(url)
            except (DataAdapterError, ValueError, TypeError):
                continue

        def read(url):
            try:
                context.check_active()
                return self._reader(url, context=context, max_chars=request.max_chars,
                                    allowed_domains=domains, mode=request.web_read_mode)
            except (DataAdapterError, RequestStopped) as exc:
                return exc
            except Exception:
                return DataAdapterError('upstream_schema')

        if request.web_read_workers == 1 or request.web_read_mode != 'http' or len(urls) < 2:
            return {url: read(url) for url in urls}
        with ThreadPoolExecutor(max_workers=min(request.web_read_workers, len(urls)),
                                thread_name_prefix='ir-search-web') as pool:
            # executor.map preserves discovery order regardless of completion order.
            return dict(zip(urls, pool.map(read, urls)))

    def search_materials(self, request, *, context):
        """Route by region and share budgets; one failed engine does not erase another."""
        if not request.published_start or context.account_scope != self.capability.account_scope:
            raise DataAdapterError("unsupported")
        if request.material_types and not set(request.material_types) & set(_KINDS):
            raise DataAdapterError("unsupported")
        preview = _web_plan(request, self._profile)
        if preview["scope_conflict"]:
            raise DataAdapterError("unsupported")
        routes = route_web_search(request, self._profile)
        page = MaterialSearchPage([], 0)
        budget = min(request.candidates_per_source, 10)
        if request.candidates_per_source > 10: page.diagnostics.append(_diag("web_candidate_limit_reached"))
        for index, route in enumerate(routes):
            candidates = budget // len(routes) + (index < budget % len(routes))
            reads = request.text_reads_per_source // len(routes) + (index < request.text_reads_per_source % len(routes))
            page.diagnostics.extend([_diag("web_route_" + route.region + "_" + route.provider),
                                     _diag("web_region_" + route.basis)])
            if not candidates:
                page.diagnostics.append(_diag("web_region_budget_exhausted"))
                page.scans.append(MaterialSourceScan(route.provider + "_web_search", MaterialKind.WEB_PAGE,
                    request.published_start, request.published_end, "web_region_budget_exhausted",
                    date_filter_basis="local_publication_metadata", discovery_provider=route.provider,
                    web_region=route.region, routing_basis=route.basis))
                continue
            try:
                part = self._search_once(replace(request, candidates_per_source=candidates, text_reads_per_source=reads),
                                         route=route, context=context)
                page.candidates.extend(part.candidates)
                page.scanned_count += part.scanned_count
                page.scans.extend(part.scans)
                page.diagnostics.extend(part.diagnostics)
            except DataAdapterError as exc:
                page.diagnostics.append(Diagnostic(exc.code, "search_materials", "web", adapter_mode=AdapterMode.LIVE,
                    failure_kind=exc.failure_kind, message=f"discovery_provider={route.provider};web_region={route.region}"))
                page.scans.append(MaterialSourceScan(route.provider + "_web_search", MaterialKind.WEB_PAGE,
                    request.published_start, request.published_end, exc.code, date_filter_basis="local_publication_metadata",
                    discovery_provider=route.provider, web_region=route.region, routing_basis=route.basis))
                if exc.code == 'quota':
                    domains, _ = _effective_domains(request, self._profile)
                    page.fallback_requests.append(WebSearchFallback(
                        route.provider, _web_query(request, domains), route.region,
                        request.published_start, request.published_end, candidates, domains,
                        request.period_start, request.period_end, max_text_reads=min(reads, candidates)))
                    page.diagnostics.append(_diag('caller_web_search_required'))
        page.diagnostics = list(dict.fromkeys(page.diagnostics))
        return page

    def _search_once(self, request, *, route, context):
        domains, conflict = _effective_domains(request, self._profile)
        if conflict:
            raise DataAdapterError('unsupported')
        query = _web_query(request, domains)
        limit = min(request.candidates_per_source, 10)
        client = {'bocha': self._bocha_client, 'exa': self._exa_client, 'anysearch': self._client}[route.provider]
        options = {'allowed_domains': domains} if isinstance(client, (BochaWebClient, ExaWebClient)) else {}
        found = client.search(query, limit=limit, context=context, **options)
        context.check_active()
        if not isinstance(found.rows, list) or len(found.rows) > 100 or any(not isinstance(r, dict) for r in found.rows):
            raise DataAdapterError("upstream_schema")
        rows = found.rows[:limit]
        page = MaterialSearchPage([], len(rows), diagnostics=[_diag("bounded_web_search"),
            _diag("web_publication_filter_local"), _diag("web_classification_inferred")], scans=[MaterialSourceScan(
                route.provider + "_web_search", MaterialKind.WEB_PAGE, request.published_start, request.published_end,
                "queried", len(found.rows), len(rows), date_filter_basis="local_publication_metadata",
                discovery_provider=route.provider, web_region=route.region, routing_basis=route.basis)])
        authenticated = bool({'bocha': self._profile.bocha_api_key, 'exa': self._profile.exa_api_key,
                              'anysearch': self._profile.api_key}[route.provider])
        if not authenticated:
            page.diagnostics.append(_diag("anonymous_web_discovery"))
        if len(found.rows) > limit or request.candidates_per_source > 10:
            page.diagnostics.append(_diag("web_candidate_limit_reached"))
        outcomes = self._prefetch(rows, request, context, domains)
        if request.web_read_workers > 1 and request.web_read_mode == 'http':
            page.diagnostics.append(_diag("bounded_parallel_web_reads"))
        elif request.web_read_workers > 1:
            page.diagnostics.append(_diag('web_parallel_reads_require_http'))
        seen = set()
        for row in rows:
            context.check_active()
            try:
                url = _field(row, "url", required=True)
                _, host, _ = _url(url)
                if not _allowed_domain(host, domains):
                    raise DataAdapterError("blocked_url")
                if url in seen:
                    page.diagnostics.append(_diag("duplicate_web_result"))
                    continue
                seen.add(url)
                title = _field(row, "title", required=True)
                snippet = _field(row, "snippet")
                warnings = ["business_period_unknown", "material_type_inferred", "publisher_identity_domain_only"]
                text, scope = snippet[:request.max_chars], TextScope.SEARCH_SNIPPET if snippet else TextScope.METADATA
                if snippet:
                    warnings.append("snippet_origin_unverified")
                if len(snippet) > request.max_chars:
                    warnings.append("text_truncated")
                published, instant = None, None
                try:
                    published, instant = _publication(row.get("published_at"))
                except ValueError:
                    warnings.append("search_publication_invalid")
                if published:
                    warnings.append("publication_from_search_metadata")
                original_url, fetched_at = url, found.fetched_at
                links, read_details = (), {}
                if url in outcomes:
                    try:
                        document = outcomes[url]
                        if isinstance(document, (DataAdapterError, RequestStopped)):
                            raise document
                        context.check_active()
                        read_details = document.extra.get("web_read", {})
                        links = tuple(document.extra.get("public_links", []))
                        content_state = read_details.get("content_state")
                        if content_state and content_state not in _VALID:
                            raise DataAdapterError("web_content_" + content_state)
                        _, document_host, _ = _url(document.url)
                        if (not _allowed_domain(document_host, domains) or document.errors or not document.text.strip()
                                or document.extra.get("adapter_mode") != "live" or document.extra.get("generated")
                                or document.content_type == "snippet" or "snippet" in document.extraction_method):
                            raise DataAdapterError("upstream_schema")
                        text, scope = document.text[:request.max_chars], TextScope.EXTRACTED_TEXT
                        host = document_host
                        title = document.title or title
                        original_url, fetched_at = document.url, document.fetched_at
                        warnings = [w for w in warnings if w not in {"snippet_origin_unverified", "text_truncated"}]
                        warnings.append("origin_text_extracted_not_independently_verified")
                        if document.warnings:
                            warnings.append("web_extraction_has_warnings")
                            warnings.extend(w for w in document.warnings if w.startswith("web_") and re.fullmatch(r"[a-z_]+", w))
                        if any("truncat" in str(w).lower() and "link" not in str(w).lower() for w in document.warnings) or len(document.text) > request.max_chars:
                            warnings.append("text_truncated")
                        if "unencrypted_public_document" in document.warnings:
                            warnings.append("unencrypted_public_document")
                        if document.published_at:
                            actual_day, actual_time = _publication(document.published_at)
                            if published and published != actual_day:
                                warnings.append("publication_date_conflict")
                            published, instant = actual_day, actual_time
                            warnings = [w for w in warnings if w != "publication_from_search_metadata"]
                            warnings.append("publication_from_page_metadata")
                    except (DataAdapterError, RequestStopped) as exc:
                        page.diagnostics.append(_diag(exc.code, exc.failure_kind))
                        warnings.append("original_text_fetch_failed")
                else:
                    warnings.append("text_not_read_within_budget")
                    page.diagnostics.append(_diag("text_read_budget_exhausted"))
                if published and not request.published_start <= published <= request.published_end:
                    page.diagnostics.append(_diag("web_outside_publication_window"))
                    continue
                if published and not instant:
                    warnings.append("publication_time_precision_unverified")
                kind, evidence, tier = _classification(host, title, scope == TextScope.EXTRACTED_TEXT)
                if read_details.get('content_state') == 'directory_links':
                    kind, evidence = MaterialKind.WEB_PAGE, EvidenceType.UNKNOWN
                    warnings.append('directory_listing_not_document_body')
                institution = _institution_for_url(original_url)
                if institution:
                    read_details = {**read_details, 'institution': institution.to_dict(),
                                    'institution_match_basis': 'official_domain_not_content_verification'}
                attachment_links = [link for link in links if link.get('kind') in {'pdf', 'attachment'}]
                if len(attachment_links) > 50:
                    warnings.append('attachment_metadata_truncated')
                authority = SourceAuthority.ANONYMOUS_SEARCH if not authenticated else SourceAuthority.COMMERCIAL_SEARCH
                if scope == TextScope.EXTRACTED_TEXT:
                    authority = SourceAuthority.UNKNOWN
                    if host == "gov.cn" or host.endswith(".gov.cn") or institution and institution.category == "regulator":
                        authority = SourceAuthority.REGULATOR
                        warnings.append("authority_inferred_from_domain")
                    elif institution:
                        authority = SourceAuthority.COMPANY
                        warnings.append("authority_inferred_from_catalog_domain")
                page.candidates.append(MaterialCandidate(url, title, kind, "web",
                    Provenance(self.name, host, fetched_at, source_tier=tier, authority=authority,
                               evidence_type=evidence, adapter_mode=AdapterMode.LIVE),
                    text=text, text_scope=scope, original_url=original_url, published_on=published, published_at=instant,
                    warnings=tuple(dict.fromkeys(warnings)), discovery_provider=route.provider,
                    links=links, read_details=read_details,
                    attachments=tuple(MaterialAttachment(link['url'], (link.get('text') or link['url'])[:1000],
                        'pdf' if link['kind'] == 'pdf' else 'unknown') for link in attachment_links[:50])))
            except DataAdapterError as exc:
                page.diagnostics.append(_diag(exc.code, exc.failure_kind))
            except (ValueError, TypeError, AttributeError):
                page.diagnostics.append(_diag("invalid_web_record", DataAdapterError.KINDS["upstream_schema"]))
        page.diagnostics = list(dict.fromkeys(page.diagnostics))
        return page
