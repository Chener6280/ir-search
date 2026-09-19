"""Deterministic, bounded evidence discovery across independent material sources."""
from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from urllib.parse import urlsplit, urlunsplit

from ir_search.context import RequestContext, RequestStopped
from ir_search.contracts import AdapterMode, Diagnostic, Status
from ir_search.contracts.materials import MaterialCandidate, MaterialSearchPage, MaterialSearchRequest, MaterialSearchResult, MaterialSourceScan, TextScope, WebSearchFallback
from ir_search.entity import load_entities
from ir_search.institutions import _match_institutions
from ir_search.material_registry import MATERIAL_SOURCE_INTENT, build_material_registry
from ir_search.models import EntityType, FailureKind, EvidenceType
from ir_search.registry import DataAdapterError

_TOPICS = {
    "动销": (("动销", "终端销售", "终端销量"), ("批价", "库存", "回款", "发货", "补货", "开瓶")),
    "渠道调研": (("渠道调研", "经销商调研", "终端调研"), ("库存", "批价", "回款")),
}


def _plan(request):
    symbols, aliases = list(request.symbols), list(request.entities)
    haystack = request.question.casefold()
    for entity in load_entities():
        if entity.entity_type != EntityType.COMPANY:
            continue
        names = entity.names + entity.aliases
        named = any(name.casefold() in haystack or name.casefold() in {v.casefold() for v in request.entities} for name in names if name)
        selected = bool(set(entity.codes) & set(symbols))
        if selected or (not request.symbols and named):
            aliases.extend(names)
            if not request.symbols:
                symbols.extend(code for code in entity.codes if re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", code))
    institutions = _match_institutions(request.question, request.entities)
    institution_aliases = list(dict.fromkeys(name for row in institutions for name in (row.name, *row.aliases)))
    aliases.extend(institution_aliases)
    topic, related = list(request.keywords), []
    for trigger, (direct, proxy) in _TOPICS.items():
        if trigger in request.question or any(trigger in word for word in request.keywords):
            topic.extend(direct)
            related.extend(proxy)
    if not topic:
        remaining = request.question
        for alias in sorted(aliases + symbols, key=len, reverse=True):
            remaining = re.sub(re.escape(alias), ' ', remaining, flags=re.I)
        remaining = re.sub(r"\d{4}年|\d{1,2}月|请问|请|帮我|我想|一下|看看|查询|检索|关于|数据|情况|如何|怎么样|的|了|吗", " ", remaining)
        topic = re.findall(r"[\u4e00-\u9fff]{2,20}|[A-Za-z][A-Za-z0-9_-]{1,30}", remaining)
    unique = lambda values: list(dict.fromkeys(values))[:20]
    return {"symbols": unique(symbols), "entity_aliases": unique(aliases),
            "institutions": [row.to_dict() for row in institutions],
            "institution_aliases": institution_aliases,
            "institution_catalog_coverage": "curated_seed_not_exhaustive",
            "topic_terms": unique(topic), "related_terms": [v for v in unique(related) if v not in topic],
            "expansion_basis": "packaged_entity_dictionary_and_explicit_rules",
            "matching_basis": "lexical_hints_not_verified_measurements",
            "publication_window": [request.published_start, request.published_end],
            "business_period": [request.period_start, request.period_end],
            "period_policy": "retain_unknown_period_with_warning_never_infer_from_publication_date",
            "source_text_trust": "untrusted"}


def _diag(code, provider=None, failure=FailureKind.NONE, mode=AdapterMode.UNKNOWN):
    return Diagnostic(code, "search_materials", provider, failure_kind=failure, adapter_mode=mode)


def _validate_page(page, cap, request):
    if (not isinstance(page, MaterialSearchPage) or not isinstance(page.candidates, list)
            or type(page.scanned_count) is not int or type(page.complete) is not bool
            or not 0 <= len(page.candidates) <= page.scanned_count <= request.candidates_per_source
            or any(not isinstance(d, Diagnostic) for d in page.diagnostics)):
        raise ValueError("Invalid material page")
    seen = set()
    if not isinstance(page.scans, list) or len(page.scans) > 16:
        raise ValueError("Invalid scan details")
    for scan in page.scans:
        if not isinstance(scan, MaterialSourceScan) or replace(scan).material_type not in cap.material_types:
            raise ValueError("Invalid scan details")
        if scan.query_start < request.published_start or scan.query_end > request.published_end:
            raise ValueError("Scan outside requested window")
    if page.scans and sum(s.inspected_count for s in page.scans) != page.scanned_count:
        raise ValueError("Scan totals disagree")
    if not isinstance(page.fallback_requests, list) or len(page.fallback_requests) > 2:
        raise ValueError('Invalid web fallback requests')
    failed_routes = {(s.discovery_provider, s.web_region) for s in page.scans if s.state == 'quota'}
    fallback_routes = set()
    for raw in page.fallback_requests:
        if not isinstance(raw, WebSearchFallback):
            raise ValueError('Invalid web fallback request')
        fallback = replace(raw)
        route_key = (fallback.failed_provider, fallback.web_region)
        if (fallback != raw or cap.provider != 'web' or route_key not in failed_routes or route_key in fallback_routes
                or fallback.published_start != request.published_start or fallback.published_end != request.published_end
                or fallback.period_start != request.period_start or fallback.period_end != request.period_end
                or fallback.max_results > min(request.candidates_per_source, 10)
                or fallback.max_text_reads > request.text_reads_per_source):
            raise ValueError('Web fallback source/scope mismatch')
        fallback_routes.add(route_key)
    if page.fallback_requests and sum(f.max_results for f in page.fallback_requests) + page.scanned_count > min(request.candidates_per_source, 10):
        raise ValueError('Web fallback exceeds shared candidate budget')
    if sum(f.max_text_reads for f in page.fallback_requests) > request.text_reads_per_source:
        raise ValueError('Web fallback exceeds shared reading budget')
    for candidate in page.candidates:
        if not isinstance(candidate, MaterialCandidate):
            raise ValueError("Untyped candidate")
        candidate = replace(candidate)  # Revalidate even a mutated/injected object.
        record_key = (candidate.source_ref, candidate.discovery_provider)
        if (candidate.provenance.provider != cap.provider or candidate.provenance.adapter_mode != AdapterMode.LIVE
                or (candidate.provenance.generated and not (
                    cap.allows_stored_summaries and candidate.text_scope == TextScope.ABSTRACT
                    and candidate.provenance.evidence_type == EvidenceType.OPINION
                    and candidate.read_details.get('content_origin') == 'provider_stored_summary'))
                or candidate.channel != cap.channel
                or candidate.material_type not in cap.material_types or record_key in seen):
            raise ValueError("Candidate source/capability mismatch")
        seen.add(record_key)
    from ir_search.infrastructure.material_cursor import _decode
    if not isinstance(page.continuation_cursors, list) or len(page.continuation_cursors) > 20:
        raise ValueError('Invalid continuation cursors')
    for token in page.continuation_cursors:
        if _decode(token)['provider'] != cap.provider: raise ValueError('Cursor provider mismatch')
    page.to_dict()


def _matching(candidate, plan, request):
    body = (candidate.title + "\n" + candidate.text + "\n" + "\n".join(a.name for a in candidate.attachments)).casefold()
    identifiers = [v for v in plan['entity_aliases'] + plan['symbols'] if v not in plan['institution_aliases']]
    institution_ids = {row['institution_id'] for row in plan['institutions']}
    original_body = candidate.title + '\n' + candidate.text + '\n' + '\n'.join(a.name for a in candidate.attachments)
    institution_hit = bool(institution_ids and institution_ids.intersection(row.institution_id for row in _match_institutions(original_body)))
    if (identifiers or institution_ids) and not (institution_hit or set(candidate.symbols) & set(plan['symbols'])
                                               or any(v.casefold() in body for v in identifiers)):
        return None
    direct = [v for v in plan["topic_terms"] if v.casefold() in body]
    related = [v for v in plan["related_terms"] if v.casefold() in body]
    if not direct and not related:
        return None
    period = "not_requested"
    if request.period_start:
        if not candidate.period_start:
            period = "unknown"
        elif candidate.period_end < request.period_start or candidate.period_start > request.period_end:
            return None
        else:
            period = "overlaps_requested_period"
    return {"topic_terms": direct, "related_terms": related,
            "classification": "topic_term_match" if direct else "related_term_match",
            "business_period": period, "interpretation": "lexical_match_only_not_confirmation_of_fact_or_metric"}


def _spans(candidate, match, version_id):
    terms = match["topic_terms"] + match["related_terms"]
    spans = []
    for part, text in (("text", candidate.text), ("title", candidate.title)):
        starts = sorted({m.start() for term in terms for m in re.finditer(re.escape(term), text, re.IGNORECASE)})
        for pos in starts:
            start, end = max(0, pos - 70), min(len(text), pos + 150)
            section = next((s for s in candidate.sections if s.start_char <= pos < s.end_char), None) if part == "text" else None
            if section:
                start, end = max(start, section.start_char), min(end, section.end_char)
            elif part == "text" and candidate.sections:
                continue
            if any(s["source_part"] == part and s["start_char"] <= pos < s["end_char"] for s in spans):
                continue
            spans.append({"version_id": version_id, "source_ref": candidate.source_ref,
                          "url": candidate.original_url, "source_part": part, "start_char": start, "end_char": end,
                          "text": text[start:end], "text_scope": candidate.text_scope.value if part == "text" else "metadata"})
            spans[-1]["source_part_hash"] = hashlib.sha256(text.encode()).hexdigest()
            spans[-1]["offset_unit"] = "unicode_code_points"
            if part == 'text' and candidate.text_scope == TextScope.EXTRACTED_TEXT:
                from ir_search.infrastructure.video_documents import _time_citation
                spans[-1].update(_time_citation(candidate.read_details, candidate.original_url or candidate.source_ref, text, start, end))
            if section:
                spans[-1].update(source_role=section.role, author=section.author,
                                 section_source_ref=section.source_ref, section_published_at=section.published_at.isoformat() if section.published_at else None)
            if len(spans) == 3:
                return spans
    for index, attachment in enumerate(candidate.attachments):
        if any(term.casefold() in attachment.name.casefold() for term in terms):
            spans.append({"version_id": version_id, "source_ref": attachment.source_ref, "url": candidate.original_url,
                          "source_part": "attachment_name", "attachment_index": index, "start_char": 0,
                          "end_char": len(attachment.name), "text": attachment.name, "text_scope": "metadata",
                          "source_part_hash": hashlib.sha256(attachment.name.encode()).hexdigest(),
                          "offset_unit": "unicode_code_points"})
            if len(spans) == 3: break
    return spans


def _group_key(candidate):
    if candidate.original_url:
        parsed = urlsplit(candidate.original_url)
        key = urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.query, ""))
        return "same_original_url", key
    # Content equivalence is checked later, respecting text scope and truncation.
    return "source_record", candidate.provenance.provider + "|" + candidate.source_ref


def _merge_copies(groups):
    """Join exact, nontruncated long copies across distinct hosting URLs."""
    parents = list(range(len(groups)))
    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index
    seen = {}
    for index, group in enumerate(groups):
        for version in group["versions"]:
            # Distinct official accession IDs are distinct filings, even if an
            # amendment happens to repeat the same visible text.
            if (version['provenance']['authority'] == 'official_filing'
                    and version.get('source_document_id')):
                continue
            if (len(version["text"]) < 100 or "text_truncated" in version["warnings"]
                    or version["text_scope"] in {TextScope.SEARCH_SNIPPET.value, TextScope.SOURCE_EXCERPT.value}):
                continue
            key = (group["material_type"], version["text_scope"], version["content_hash"])
            if key in seen:
                parents[root(index)] = root(seen[key])
            else:
                seen[key] = index
    merged = {}
    for index, group in enumerate(groups):
        key = root(index)
        if key not in merged:
            merged[key] = group
        else:
            target = merged[key]
            target["versions"].extend(group["versions"])
            target["rank_score"] = max(target["rank_score"], group["rank_score"])
            target["document_group_id"] = min(target["document_group_id"], group["document_group_id"])
            target["grouping_basis"] = "identical_text_across_locations"
    return list(merged.values())


def _returned_evidence(groups, provider):
    """Adapt the legacy actual-evidence audit to typed material output, not claims.

    Count only retained versions after grouping and the result limit. A title
    citation, abstract or truncated text is never counted as a full-file read.
    """
    versions = [version for group in groups for version in group["versions"]
                if version["provenance"]["provider"] == provider]
    spans = [span for version in versions for span in version["evidence_spans"]]
    return {
        "scope": "returned_items_after_limit",
        "document_groups": sum(any(v["provenance"]["provider"] == provider for v in group["versions"])
                               for group in groups),
        "source_records": len(versions),
        "text_scope_counts": {scope.value: sum(v["text_scope"] == scope.value for v in versions)
                              for scope in TextScope},
        "citation_scope_counts": {scope.value: sum(s["text_scope"] == scope.value for s in spans)
                                  for scope in TextScope},
        "truncated_records": sum("text_truncated" in v["warnings"] for v in versions),
        "publication_date_unknown_records": sum(not v["published_on"] for v in versions),
        "business_period_unverified_records": sum(v["match"]["business_period"] == "unknown" for v in versions),
    }


def search_materials(request: MaterialSearchRequest, *, registry=None, context=None, audit_dir=None) -> MaterialSearchResult:
    """Search with optional private operational recording; default writes no run log."""
    if audit_dir is not None:
        from pathlib import Path
        if (not isinstance(audit_dir, (str, Path)) or not str(audit_dir).strip()
                or any(ord(c) < 32 for c in str(audit_dir))): raise ValueError('Invalid audit directory')
    result = _search_materials(request, registry=registry, context=context)
    if audit_dir is not None:
        from .material_runs import record_material_run
        result.audit = record_material_run(result, audit_dir)
    return result


def _search_materials(request: MaterialSearchRequest, *, registry=None, context=None) -> MaterialSearchResult:
    """Return evidence groups and explicit source/date/coverage gaps; no LLM calls."""
    if not isinstance(request, MaterialSearchRequest):
        raise ValueError("MaterialSearchRequest required")
    context = context if context is not None else RequestContext(max_operations=100)
    registry = registry if registry is not None else build_material_registry()
    result = MaterialSearchResult(request, context.request_id, plan=_plan(request))
    result.diagnostics.extend(registry.diagnostics)
    if not request.published_start:
        result.required_inputs.extend(["published_start", "published_end"])
    if not request.period_start and re.search(r"\d{1,2}月", request.question):
        result.required_inputs.extend(["period_start", "period_end"])
    if not result.plan["topic_terms"]:
        result.required_inputs.append("keywords")
    available = {adapter.name: adapter for adapter in registry.entries(account_scope=context.account_scope)}
    result.plan['source_selection_policy'] = 'explicit_user_choice'
    if not request.providers:
        result.required_inputs.append('providers')
        result.plan.update(selected_providers=[], source_plans=[], source_calls_started=0,
                           execution_mode='preview' if request.dry_run else 'awaiting_source_selection')
        result.plan['source_options'] = [
            {'provider': name, 'channel': adapter.capability.channel,
             'material_types': [kind.value for kind in adapter.capability.material_types],
             'requires_symbols': adapter.capability.requires_symbols,
             'max_symbols': adapter.capability.max_symbols,
             'excluded_by_request': name in request.exclude_providers,
             'adapter_mode': adapter.capability.adapter_mode.value,
             'verification': 'registration_only_not_live_probe'}
            for name, adapter in available.items()]
        result.plan['unregistered_providers'] = sorted(set(MATERIAL_SOURCE_INTENT) - set(available))
        result.diagnostics.append(_diag('material_source_selection_required'))
        result.gaps.append({'code': 'material_source_selection_required', 'source_calls_started': 0})
        if len(result.required_inputs) > 1:
            result.diagnostics.append(_diag('research_scope_required'))
        if request.dry_run:
            result.diagnostics.append(_diag('dry_run_no_source_calls'))
        result.to_dict()
        return result
    if result.required_inputs and not request.dry_run:
        result.diagnostics.append(_diag("research_scope_required"))
        return result
    request_for_source = replace(request, symbols=tuple(result.plan["symbols"]))
    names = list(request.providers)
    selected = []
    for name in names:
        state = {"provider": name, "state": "not_registered", "scanned_count": 0, "matched_count": 0}
        result.coverage.append(state)
        if name in request.exclude_providers:
            state["state"] = "excluded_by_request"
            continue
        adapter = available.get(name)
        if adapter is None:
            result.gaps.append({"code": "material_source_not_registered", "provider": name})
            continue
        cap = adapter.capability
        reason = None
        if cap.adapter_mode != AdapterMode.LIVE:
            reason = "non_live_material_source_blocked"
        elif request.material_types and not set(request.material_types) & set(cap.material_types):
            reason = "material_type_not_supported"
        elif cap.requires_symbols and not request_for_source.symbols:
            reason = "source_requires_symbols"
        elif name in {'hkex','company_ir'} and any(not re.fullmatch(r'\d{4,5}\.HK',s) for s in request_for_source.symbols):
            reason = 'source_symbol_format_not_supported'
        elif name == 'sec' and not (request_for_source.symbols or request_for_source.sec_ciks):
            reason = 'sec_company_scope_required'
        elif len(request_for_source.symbols) > cap.max_symbols:
            reason = "source_symbol_limit"
        elif len(selected) >= request.max_sources:
            reason = "source_budget_exhausted"
        if reason:
            state["state"] = reason
            result.gaps.append({"code": reason, "provider": name})
        else:
            state["state"] = "selected"
            state["channel"] = cap.channel
            state["search_basis"] = cap.search_basis
            selected.append((adapter, state))
    result.plan["selected_providers"] = [adapter.name for adapter, _ in selected]
    from ir_search.adapters.web_materials import WebMaterialAdapter
    from ir_search.infrastructure.web_material_plan import _web_plan
    result.plan['execution_mode'] = 'preview' if request.dry_run else 'execute'
    result.plan['budget'] = {
        'max_source_dispatches': len(selected), 'max_candidates_per_source': request.candidates_per_source,
        'max_text_reads_per_source': request.text_reads_per_source, 'max_returned_groups': request.limit,
        'max_chars_per_record': request.max_chars, 'max_operations': context.max_operations,
        'operations_already_used': context.operations, 'operations_remaining': max(0, context.max_operations - context.operations),
        'timeout_seconds': context.timeout_seconds, 'remaining_seconds': context.remaining_seconds(),
        'cost_estimate': None, 'cost_basis': 'provider_pricing_and_entitlements_not_queried',
        'limits_are_caps_not_expected_results': True}
    result.plan['source_plans'] = []
    for adapter, state in selected:
        source_plan = {'provider': adapter.name, 'verification': 'registration_only_not_live_probe',
                       'max_candidates': request.candidates_per_source,
                       'max_text_reads': request.text_reads_per_source,
                       'network_attempts_upper_bound': None}
        # Only the built-in pure planner is used. Never call arbitrary adapter
        # methods during preview, including a third-party "preview" hook.
        if type(adapter) is WebMaterialAdapter:
            source_plan.update(_web_plan(request_for_source, adapter._profile))
        from ir_search.adapters.rss_materials import RSSMaterialAdapter
        if type(adapter) is RSSMaterialAdapter:
            source_plan.update(feed_count=len(adapter._profile.feed_urls), read_mode=request.web_read_mode,
                max_discovery_calls=len(adapter._profile.feed_urls),
                coverage_scope='configured_feed_snapshots_not_historical_archive',
                publication_filter='local_publication_metadata_updated_is_not_published',
                feed_excerpt_is_original_body=False, background_polling=False)
        if type(adapter) in {WebMaterialAdapter, RSSMaterialAdapter}:
            source_plan['cloud_reader_explicit'] = request.web_read_mode == 'firecrawl'
            source_plan['max_cloud_scrapes'] = source_plan['max_text_reads'] if request.web_read_mode == 'firecrawl' else 0
        from ir_search.adapters.sec_materials import SECMaterialAdapter
        if type(adapter) is SECMaterialAdapter:
            from ir_search.infrastructure.sec import _DEFAULT_FORMS
            count = min(5, len(request_for_source.symbols) + len(request_for_source.sec_ciks))
            source_plan.update(sec_forms=list(request_for_source.sec_forms or _DEFAULT_FORMS),
                sec_ciks=list(request_for_source.sec_ciks),
                coverage_scope='us_edgar_filers_including_foreign_issuers',
                index_filter='filing_date_and_exact_form', exhibit_policy='explicit_retrieve_only',
                max_history_files_per_company=adapter._profile.max_history_files,
                network_attempts_upper_bound=int(bool(request_for_source.symbols)) + count * (1 + adapter._profile.max_history_files) + request_for_source.text_reads_per_source)
        from ir_search.adapters.platform_materials import XueqiuMaterialAdapter, EastmoneyMaterialAdapter, VideoMaterialAdapter, XiaoyuzhouMaterialAdapter, _platform_plan
        if type(adapter) in {XueqiuMaterialAdapter, EastmoneyMaterialAdapter, VideoMaterialAdapter, XiaoyuzhouMaterialAdapter}:
            source_plan.update(_platform_plan(adapter.name, request_for_source, adapter._profile))
        from ir_search.adapters.xhs_materials import XhsMaterialAdapter
        if type(adapter) is XhsMaterialAdapter:
            source_plan.update(backend='optional_local_xiaohongshu_mcp_http',sort=request.xhs_sort,
                comment_limit_per_note=request.xhs_comment_limit,cache_mode=request.xhs_cache_mode,
                coverage_scope='one_search_snapshot_with_local_date_filter',
                continuation_scope='uninspected_snapshot_records_only',
                network_attempts_upper_bound=2+request.text_reads_per_source)
        result.plan['source_plans'].append(source_plan)
    if request.dry_run:
        for _, state in selected:
            state['state'] = 'planned_not_queried'
        result.plan['source_calls_started'] = 0
        result.diagnostics.append(_diag('dry_run_no_source_calls'))
        if result.required_inputs:
            result.diagnostics.append(_diag('research_scope_required'))
        result.gaps.append({'code': 'preview_not_evidence_or_coverage_verification'})
        result.status = Status.PARTIAL if selected and not result.required_inputs else Status.UNAVAILABLE
        result.to_dict()
        return result
    groups, succeeded = {}, 0
    for index, (adapter, state) in enumerate(selected):
        cap = adapter.capability
        try:
            context.begin_operation()
            page = adapter.search_materials(request_for_source, context=context)
            context.check_active()
            _validate_page(page, cap, request)
            result.fallback_requests.extend(page.fallback_requests)
            if page.fallback_requests:
                result.gaps.append({'code': 'caller_web_search_pending', 'provider': adapter.name,
                                    'target': 'caller_native_web_search', 'completed': False})
            state['continuation_cursors'] = list(page.continuation_cursors)
            queried = not page.scans or any(scan.state == "queried" for scan in page.scans)
            state.update(state="queried" if queried else "source_queries_failed", scanned_count=page.scanned_count, source_page_complete=page.complete)
            if page.scans:
                state["scans"] = [scan.to_dict() for scan in page.scans]
            succeeded += int(queried)
            if not queried:
                result.gaps.append({"code": "source_queries_failed", "provider": adapter.name})
            result.diagnostics.extend(_diag(d.code, adapter.name, d.failure_kind, cap.adapter_mode) for d in page.diagnostics)
            if not page.complete:
                result.gaps.append({"code": "source_scan_incomplete", "provider": adapter.name})
            if not cap.supports_publication_filter:
                result.gaps.append({"code": "publication_filter_applied_locally", "provider": adapter.name})
            for raw in page.candidates:
                context.check_active()
                sections = tuple(replace(s, end_char=min(s.end_char, request.max_chars)) for s in raw.sections if s.start_char < request.max_chars)
                candidate = replace(raw, text=raw.text[:request.max_chars], sections=sections, warnings=tuple(dict.fromkeys(
                    raw.warnings + (("text_truncated",) if len(raw.text) > request.max_chars else ()))))
                if request.material_types and candidate.material_type not in request.material_types:
                    continue
                if candidate.published_on and not request.published_start <= candidate.published_on <= request.published_end:
                    result.diagnostics.append(_diag("candidate_outside_publication_window", adapter.name, FailureKind.UPSTREAM_SCHEMA, cap.adapter_mode))
                    continue
                match = _matching(candidate, result.plan, request)
                if match is None:
                    continue
                state["matched_count"] += 1
                warnings = list(candidate.warnings)
                if not candidate.published_on:
                    warnings.append("publication_date_unknown")
                if match["business_period"] == "unknown":
                    warnings.append("business_period_not_verified")
                if candidate.text_scope == TextScope.ABSTRACT:
                    warnings.append("abstract_not_full_text")
                if candidate.text_scope == TextScope.METADATA:
                    warnings.append("metadata_only")
                if candidate.text_scope == TextScope.SEARCH_SNIPPET:
                    warnings.append("search_snippet_not_original_text")
                if candidate.text_scope == TextScope.SOURCE_EXCERPT:
                    warnings.append("source_excerpt_completeness_unverified")
                basis, key = _group_key(candidate)
                group_key = candidate.material_type.value + "|" + basis + "|" + key
                content_hash = hashlib.sha256(candidate.text.encode()).hexdigest() if candidate.text else None
                version_id = "sha256:" + hashlib.sha256((candidate.title + "\n" + candidate.text).encode()).hexdigest()
                version = {**candidate.to_dict(), "content_hash": content_hash, "version_id": version_id,
                           "warnings": sorted(set(warnings)), "match": match, "evidence_spans": _spans(candidate, match, version_id)}
                score = (100 if match["topic_terms"] else 0) + 5 * len(match["topic_terms"]) + len(match["related_terms"])
                score += int(candidate.provenance.source_tier or 0)
                score += 2 if candidate.text_scope == TextScope.EXTRACTED_TEXT else 1 if candidate.text_scope == TextScope.ABSTRACT else 0
                group = groups.setdefault(group_key, {"document_group_id": "material_" + hashlib.sha256(group_key.encode()).hexdigest()[:24],
                    "grouping_basis": basis, "material_type": candidate.material_type.value,
                    "independence": "not_established", "versions": [], "rank_score": score})
                group["versions"].append(version)
                group["rank_score"] = max(score, group["rank_score"])
            if queried and not state["matched_count"]:
                result.gaps.append({"code": "no_match_in_scanned_records", "provider": adapter.name})
            context.check_active()
        except RequestStopped as exc:
            state["state"] = exc.code
            result.diagnostics.append(_diag(exc.code, adapter.name, exc.failure_kind, cap.adapter_mode))
            for _, pending in selected[index + 1:]:
                pending["state"] = "not_queried_request_stopped"
                result.gaps.append({"code": "not_queried_request_stopped", "provider": pending["provider"]})
            break
        except DataAdapterError as exc:
            state["state"] = exc.code
            result.diagnostics.append(_diag(exc.code, adapter.name, exc.failure_kind, cap.adapter_mode))
            result.gaps.append({"code": exc.code, "provider": adapter.name})
        except Exception:
            state["state"] = "invalid_material_response"
            result.diagnostics.append(_diag("invalid_material_response", adapter.name, FailureKind.UPSTREAM_SCHEMA, cap.adapter_mode))
            result.gaps.append({"code": "invalid_material_response", "provider": adapter.name})
    merged = _merge_copies(list(groups.values()))
    result.items = sorted(merged, key=lambda group: (-group["rank_score"], group["document_group_id"]))[:request.limit]
    if len(merged) > request.limit:
        result.gaps.append({"code": "result_limit_reached"})
    for group in result.items:
        group["retrieval_providers"] = sorted({v["provenance"]["provider"] for v in group["versions"]})
        group["duplicate_group"] = len(group["versions"]) > 1
    for state in result.coverage:
        state["returned_evidence"] = _returned_evidence(result.items, state["provider"])
    if request.period_start and not any(v["match"]["business_period"] == "overlaps_requested_period" for g in result.items for v in g["versions"]):
        result.gaps.append({"code": "no_evidence_with_verified_business_period"})
    if result.items and not any(v["match"]["topic_terms"] for g in result.items for v in g["versions"]):
        result.gaps.append({"code": "related_terms_only_not_direct_topic_evidence"})
    result.status = Status.PARTIAL if succeeded or result.items else Status.UNAVAILABLE
    result.diagnostics.append(_diag("bounded_search_not_exhaustive"))
    result.to_dict()
    return result
