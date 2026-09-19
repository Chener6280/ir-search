"""Company-scoped SEC disclosures, separate from normalized FMP financial data."""
from dataclasses import replace

from ir_search.context import RequestStopped
from ir_search.contracts import AdapterMode, Diagnostic, Provenance
from ir_search.contracts.materials import (MaterialCandidate, MaterialCapability, MaterialKind,
    MaterialSearchPage, MaterialSourceScan, TextScope)
from ir_search.infrastructure.sec import (_DEFAULT_FORMS, _filings, _resolve_symbols, read_sec_document)
from ir_search.models import EvidenceType, FailureKind, SourceAuthority, SourceTier
from ir_search.registry import DataAdapterError


class SECMaterialAdapter:
    name = 'sec'

    def __init__(self, profile):
        self._profile = profile
        self.capability = MaterialCapability('sec', 'official_filings', (MaterialKind.ANNOUNCEMENT,),
            max_symbols=5, search_basis='sec_submissions_local_matching', supports_publication_filter=False,
            coverage_notes=('US EDGAR filers only, including foreign issuers; not global exchange coverage.',
                'Explicit symbols or sec_ciks required; no fuzzy company resolution or market-wide full-text search.',
                'Default forms: 10-K/10-Q/8-K/20-F/40-F/6-K and amendments; sec_forms overrides exactly.',
                'Filing date differs from report date; reportDate alone never supplies a full business period.',
                'Bounded history, candidates and primary-text reads; exhibits are separate explicit reads.'))

    def search_materials(self, request, *, context):
        page = MaterialSearchPage([], 0, complete=False)
        def diagnostic(code, failure=FailureKind.NONE):
            page.diagnostics.append(Diagnostic(code, 'search_materials', self.name,
                failure_kind=failure, adapter_mode=AdapterMode.LIVE))
        if not request.symbols and not request.sec_ciks:
            raise DataAdapterError('sec_company_scope_required')
        mapping = _resolve_symbols(request.symbols, self._profile, context)
        ciks = tuple(dict.fromkeys((*request.sec_ciks, *mapping.values())))
        if len(ciks) > 5: raise DataAdapterError('sec_company_scope_required')
        forms = request.sec_forms or _DEFAULT_FORMS
        all_rows = []
        for cik in ciks:
            try:
                rows, fetched_at, history_limited = _filings(cik, self._profile, context,
                    request.published_start, request.published_end)
                filtered = [row for row in rows if row.form in forms
                    and request.published_start <= row.filing_date <= request.published_end]
                # Scan counts describe eligible index records, not every row of the JSON payload.
                filtered.sort(key=lambda row: (row.filing_date, row.accession), reverse=True)
                all_rows.extend((row, fetched_at, cik) for row in filtered)
                page.scans.append(MaterialSourceScan('sec_submissions', MaterialKind.ANNOUNCEMENT,
                    request.published_start, request.published_end, 'queried', received_count=min(len(filtered), 1000),
                    collection_id=cik, symbols=tuple(row for row, mapped in mapping.items() if mapped == cik),
                    date_filter_basis='local_publication_metadata', has_more=history_limited or len(filtered) > 1000,
                    fetched_at=fetched_at))
                if history_limited: diagnostic('sec_history_limit_reached')
                if len(filtered) > 1000: diagnostic('sec_index_count_capped')
            except DataAdapterError as exc:
                diagnostic(exc.code, exc.failure_kind)
                page.scans.append(MaterialSourceScan('sec_submissions', MaterialKind.ANNOUNCEMENT,
                    request.published_start, request.published_end, 'failed', collection_id=cik,
                    date_filter_basis='local_publication_metadata'))
        all_rows.sort(key=lambda entry: (entry[0].filing_date, entry[0].accession), reverse=True)
        chosen = all_rows[:request.candidates_per_source]
        if len(all_rows) > len(chosen): diagnostic('sec_candidate_limit_reached')
        for i, scan in enumerate(page.scans):
            count = sum(cik == scan.collection_id for _, _, cik in chosen)
            page.scans[i] = replace(scan, inspected_count=count,
                has_more=scan.has_more or scan.received_count > count if scan.state == 'queried' else None)
        page.scanned_count = len(chosen)
        reads = 0
        for row, fetched_at, _ in chosen:
            # Index entries occasionally have no downloadable primary document.
            if not row.primary_document or not row.primary_document.lower().endswith(('.htm', '.html', '.pdf')):
                diagnostic('sec_primary_document_unavailable')
                continue
            title = f'{row.company} ({", ".join(row.symbols) or "CIK " + row.cik}) {row.form} {row.filing_date.isoformat()}'
            if row.description: title += ' ' + row.description
            financial = row.form.removesuffix('/A') in {'10-K', '10-Q', '20-F', '40-F'}
            candidate = MaterialCandidate(row.url, title, MaterialKind.ANNOUNCEMENT, 'official_filings',
                Provenance('sec', row.company, fetched_at, authority=SourceAuthority.OFFICIAL_FILING,
                    source_tier=SourceTier.EXCHANGE_FILING,
                    evidence_type=EvidenceType.FINANCIAL_REPORT if financial else EvidenceType.ANNOUNCEMENT,
                    adapter_mode=AdapterMode.LIVE), original_url=row.url, symbols=row.symbols,
                published_on=row.filing_date, source_document_id=row.accession,
                collection_id=row.cik, collection_name=row.company, source_record_type=row.form,
                read_details={'filing': row._details(), 'content_origin': 'sec_submissions_metadata'},
                warnings=('business_period_not_verified', 'sec_exhibits_not_automatically_read'))
            if reads < request.text_reads_per_source:
                reads += 1
                try:
                    document = read_sec_document(row.url, context=context, max_chars=request.max_chars,
                                                 profile=self._profile, filing=row)
                    candidate = replace(candidate, text=document.text, text_scope=TextScope.EXTRACTED_TEXT,
                        provenance=replace(candidate.provenance, fetched_at=document.fetched_at), text_provider='sec',
                        links=tuple(document.extra.get('public_links', [])),
                        read_details=document.extra['material_read'],
                        warnings=tuple(dict.fromkeys(candidate.warnings + tuple(document.warnings))))
                except (DataAdapterError, RequestStopped) as exc:
                    diagnostic(exc.code, exc.failure_kind)
                    candidate = replace(candidate, warnings=candidate.warnings + ('sec_primary_text_unavailable',))
            page.candidates.append(candidate)
        diagnostic('sec_index_counts_are_date_and_form_filtered')
        diagnostic('sec_bounded_primary_text_search')
        return page
