"""Tushare research abstracts and vendor-provided policy/news HTML material adapter."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import json
import re
from zoneinfo import ZoneInfo

from ir_search.contracts import AdapterMode, Diagnostic, Provenance
from ir_search.contracts.materials import (MaterialCandidate, MaterialCapability, MaterialKind,
    MaterialSearchPage, MaterialSearchRequest, MaterialSourceScan, TextScope)
from ir_search.documents.html import ArticleHTMLParser
from ir_search.infrastructure.credentials import TushareCorpusProfile
from ir_search.infrastructure.tushare_corpus import TushareCorpusClient, FIELDS, ROW_LIMITS
from ir_search.models import EvidenceType, SourceAuthority, SourceTier
from ir_search.registry import DataAdapterError

_TOOLS = {MaterialKind.RESEARCH_REPORT: "research_report", MaterialKind.NEWS: "major_news", MaterialKind.POLICY: "npr"}
_DATES = {"research_report": "trade_date", "major_news": "pub_time", "npr": "pubtime"}
_TEXT = {"research_report": "abstr", "major_news": "content", "npr": "content_html"}


def _diag(code, failure=None):
    options = {"failure_kind": failure} if failure else {}
    return Diagnostic(code, "search_materials", "tushare_corpus", adapter_mode=AdapterMode.LIVE, **options)


def _string(row, key, *, required=False):
    value = row.get(key)
    if value is None:
        if required:
            raise ValueError("Required corpus field missing")
        return ""
    if not isinstance(value, str):
        raise ValueError("Unexpected corpus field type")
    if required and not value.strip():
        raise ValueError("Required corpus field empty")
    return value.strip()


def _published(row, tool):
    value = _string(row, _DATES[tool], required=True)
    if tool == "research_report":
        if not re.fullmatch(r"\d{8}", value):
            raise ValueError("Invalid report date")
        return datetime.strptime(value, "%Y%m%d").date(), None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", value):
        raise ValueError("Invalid corpus timestamp")
    instant = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return instant.date(), instant


def _candidate(row, tool, *, fetched_at, max_chars, read_text):
    title = _string(row, "title", required=True)
    published, instant = _published(row, tool)
    warnings = ["vendor_text_not_original_file", "publication_time_precision_unverified", "business_period_unknown"]
    url = _string(row, "url") or None
    publisher_key = {"research_report": "inst_csname", "major_news": "src", "npr": "puborg"}[tool]
    publisher = _string(row, publisher_key) or "unknown"
    if publisher == "unknown":
        warnings.append("publisher_unknown")
    kind = next(k for k, value in _TOOLS.items() if value == tool)
    tier, evidence = {"research_report": (SourceTier.BROKER, EvidenceType.BROKER_REPORT),
                      "major_news": (SourceTier.MEDIA, EvidenceType.NEWS),
                      "npr": (SourceTier.REGULATOR, EvidenceType.POLICY_DOC)}[tool]
    if publisher == "unknown": tier = None
    code = _string(row, "ts_code") if tool == "research_report" else ""
    if code and not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", code):
        raise ValueError("Unsupported report symbol")
    document_id = None
    if tool != "major_news":
        document_id = _string(row, "report_code" if tool == "research_report" else "pcode") or None
    authors = tuple(dict.fromkeys(v.strip() for v in re.split(r"[,，、;；]", _string(row, "author")) if v.strip())) if tool == "research_report" else ()
    text, scope = "", TextScope.METADATA
    raw = _string(row, _TEXT[tool])
    if raw and read_text:
        parser = ArticleHTMLParser(url or "https://api.tushare.pro/")
        parser.feed(raw)
        parser.close()
        text = parser.extracted_text()
        if text:
            scope = TextScope.ABSTRACT if tool == "research_report" else TextScope.EXTRACTED_TEXT
            if len(text) > max_chars:
                warnings.append("text_truncated")
                text = text[:max_chars]
            if tool != "research_report":
                warnings.append("vendor_html_extracted")
        else:
            warnings.append("no_extracted_text")
    elif not read_text:
        warnings.append("text_not_read_within_budget")
    else:
        warnings.append("upstream_text_missing")
    if instant:
        warnings.append("publication_timezone_assumed_shanghai")
    # No invented publisher URL. Record identity preserves location and timestamp;
    # the service separately versions the returned text and groups identical copies.
    identity = json.dumps([tool, document_id, url, code, str(published), str(instant), title,
                           hashlib.sha256(raw.encode()).hexdigest()], ensure_ascii=False)
    source_ref = "tushare-corpus://" + tool + "/" + hashlib.sha256(identity.encode()).hexdigest()
    return MaterialCandidate(source_ref, title, kind, "corpus",
        Provenance("tushare_corpus", publisher, fetched_at, authority=SourceAuthority.DATA_VENDOR,
                   source_tier=tier, evidence_type=evidence, adapter_mode=AdapterMode.LIVE),
        text=text, text_scope=scope, original_url=url, symbols=(code,) if code else (),
        published_on=published, published_at=instant, warnings=tuple(warnings + ["original_publisher_unverified"]), authors=authors, source_document_id=document_id,
        read_details={"transmission": "vendor_transcription", "publisher_verification": "vendor_claim_unverified",
                      "original_file_verified": False})


class TushareCorpusAdapter:
    name = "tushare_corpus"

    def __init__(self, profile: TushareCorpusProfile, *, client_factory=None):
        if not isinstance(profile, TushareCorpusProfile):
            raise ValueError("TushareCorpusProfile required")
        self._profile = profile
        self._client_factory = client_factory or TushareCorpusClient
        self.capability = MaterialCapability(self.name, "corpus", tuple(_TOOLS), max_symbols=1,
            coverage_notes=(
                "Research abstracts: at most the last 31 requested calendar days for one A-share symbol; otherwise last day",
                "News and policy: last requested calendar day only; news uses the configured publisher filter",
                "Bounded vendor records, local lexical matching; no remote keyword search or exhaustive pagination",
                "Embedded news/policy text parsing shares text_reads_per_source; abstracts need no separate full-text read",
                "Vendor transcriptions and supplied links are not independently verified original files",))

    def search_materials(self, request: MaterialSearchRequest, *, context) -> MaterialSearchPage:
        """Fetch independently bounded content types and keep partial successes."""
        if (not isinstance(request, MaterialSearchRequest) or not request.published_start
                or len(request.symbols) > 1 or context.account_scope != self.capability.account_scope
                or any(not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", symbol) for symbol in request.symbols)):
            raise DataAdapterError("unsupported")
        kinds = [kind for kind in _TOOLS if not request.material_types or kind in request.material_types]
        if not kinds:
            raise DataAdapterError("unsupported")
        active = kinds[:min(self._profile.max_calls_per_query, request.candidates_per_source)]
        body_kinds = [kind for kind in active if kind != MaterialKind.RESEARCH_REPORT]
        body_limits = {kind: request.text_reads_per_source // len(body_kinds) + (i < request.text_reads_per_source % len(body_kinds))
                       for i, kind in enumerate(body_kinds)}
        page = MaterialSearchPage([], 0, diagnostics=[_diag("bounded_corpus_scan"), _diag("vendor_original_not_verified")])
        client = self._client_factory(self._profile)
        successes, errors, seen = 0, [], set()
        for index, kind in enumerate(kinds):
            context.check_active()
            tool = _TOOLS[kind]
            days = 31 if kind == MaterialKind.RESEARCH_REPORT and request.symbols else 1
            start = max(request.published_start, request.published_end - timedelta(days=days - 1))
            scan = MaterialSourceScan(tool, kind, start, request.published_end, "not_queried_budget",
                publisher_filter=self._profile.news_source if kind == MaterialKind.NEWS else None,
                symbols=request.symbols if kind == MaterialKind.RESEARCH_REPORT else ())
            if kind not in active:
                page.scans.append(scan)
                page.diagnostics.append(_diag("material_type_budget_exhausted"))
                continue
            allowance = request.candidates_per_source // len(active) + (index < request.candidates_per_source % len(active))
            include_text = kind == MaterialKind.RESEARCH_REPORT or body_limits[kind] > 0
            fields = [f for f in FIELDS[tool] if include_text or f != _TEXT[tool]]
            report = kind == MaterialKind.RESEARCH_REPORT
            arguments = {"start_date": start.strftime("%Y%m%d") if report else start.isoformat() + " 00:00:00",
                         "end_date": request.published_end.strftime("%Y%m%d") if report else request.published_end.isoformat() + " 23:59:59", "fields": fields}
            if report and request.symbols:
                arguments["ts_code"] = request.symbols[0]
            if kind == MaterialKind.NEWS:
                arguments["src"] = self._profile.news_source
            if start != request.published_start:
                page.diagnostics.append(_diag("corpus_query_window_restricted"))
            try:
                response = client.fetch(tool, arguments, context=context)
                context.check_active()
                successes += 1
                # Provider order is retained and disclosed; no hidden local reordering of unscanned rows.
                rows = response.rows[:allowance]
                page.scanned_count += len(rows)
                page.scans.append(replace(scan, state="queried", received_count=len(response.rows), inspected_count=len(rows)))
                if len(response.rows) > allowance:
                    page.diagnostics.append(_diag("corpus_candidate_budget_exhausted"))
                if len(response.rows) >= ROW_LIMITS[tool]:
                    page.diagnostics.append(_diag("corpus_upstream_row_cap_reached"))
                if len(response.rows) > ROW_LIMITS[tool]:
                    page.diagnostics.append(_diag("corpus_documented_row_limit_exceeded"))
                if not response.rows:
                    page.diagnostics.append(_diag("corpus_empty_window"))
                for row_index, row in enumerate(rows):
                    context.check_active()
                    try:
                        candidate = _candidate(row, tool, fetched_at=response.fetched_at, max_chars=request.max_chars,
                            read_text=report or row_index < body_limits[kind])
                        if not start <= candidate.published_on <= request.published_end:
                            raise ValueError("Outside corpus window")
                        if report and request.symbols and candidate.symbols != request.symbols:
                            raise ValueError("Report symbol mismatch")
                        if kind == MaterialKind.NEWS and candidate.provenance.publisher != self._profile.news_source:
                            raise ValueError("News publisher mismatch")
                        if candidate.source_ref in seen:
                            page.diagnostics.append(_diag("duplicate_corpus_record"))
                            continue
                        seen.add(candidate.source_ref)
                        page.candidates.append(candidate)
                        if "text_not_read_within_budget" in candidate.warnings:
                            page.diagnostics.append(_diag("text_read_budget_exhausted"))
                    except (ValueError, TypeError, KeyError):
                        page.diagnostics.append(_diag("invalid_corpus_record", DataAdapterError.KINDS["upstream_schema"]))
            except DataAdapterError as exc:
                errors.append(exc)
                page.scans.append(replace(scan, state=exc.code))
                page.diagnostics.append(_diag(exc.code, exc.failure_kind))
        if not successes and errors:
            raise errors[0]
        page.diagnostics = list(dict.fromkeys(page.diagnostics))
        return page
