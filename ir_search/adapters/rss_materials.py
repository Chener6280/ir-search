"""RSS discovers material; the linked page separately supplies original text."""
from __future__ import annotations

from urllib.parse import urlsplit
import hashlib
import re

from ir_search.contracts import AdapterMode, Diagnostic, Provenance
from ir_search.contracts.materials import (MaterialCandidate, MaterialCapability, MaterialKind,
    MaterialSearchPage, MaterialSourceScan, TextScope)
from ir_search.infrastructure.rss import RSSProfile, fetch_feed
from ir_search.infrastructure.web_documents import read_web_document, _VALID
from ir_search.infrastructure.public_web import _url
from ir_search.models import EvidenceType, SourceAuthority, SourceTier
from ir_search.registry import DataAdapterError

_KINDS = (MaterialKind.WEB_PAGE, MaterialKind.NEWS, MaterialKind.POLICY)


def _diag(code, failure=None):
    return Diagnostic(code, 'search_materials', 'rss', adapter_mode=AdapterMode.LIVE,
                      **({'failure_kind': failure} if failure is not None else {}))


class RSSMaterialAdapter:
    name = 'rss'

    def __init__(self, profile: RSSProfile, *, client=None, reader=None):
        self._profile, self._client, self._reader = profile, client or fetch_feed, reader or read_web_document
        self.capability = MaterialCapability(self.name, 'feed', _KINDS,
            search_basis='bounded_feed_snapshot_local_matching', supports_publication_filter=False,
            coverage_notes=('configured_public_feeds_only', 'feed_excerpt_not_article_body',
                            'snapshot_not_historical_archive', 'no_background_polling'))

    def search_materials(self, request, *, context):
        """Inspect a bounded feed snapshot and explicitly budget original-page reads."""
        page = MaterialSearchPage([], 0, complete=False)
        page.diagnostics.append(_diag('rss_snapshot_not_historical_archive'))
        from ir_search.infrastructure.material_cursor import _decode
        if any(_decode(token)['provider'] == self.name for token in request.source_cursors):
            page.diagnostics.append(_diag('rss_continuation_unsupported'))
            return page
        reads, seen = 0, set()
        for index, feed_url in enumerate(self._profile.feed_urls):
            context.check_active()
            remaining = request.candidates_per_source - page.scanned_count
            if remaining <= 0:
                page.diagnostics.append(_diag('rss_candidate_budget_exhausted'))
                break
            # Allocate some inspection budget to each feed, in configuration order.
            quota = max(1, remaining // (len(self._profile.feed_urls) - index))
            try:
                feed = self._client(feed_url, context=context)
            except DataAdapterError as exc:
                page.diagnostics.append(_diag(exc.code, exc.failure_kind))
                page.scans.append(MaterialSourceScan('rss_snapshot', MaterialKind.WEB_PAGE,
                    request.published_start, request.published_end, 'failed', collection_id=feed_url,
                    date_filter_basis='local_publication_metadata'))
                continue
            rows = feed.items[:quota]
            page.scanned_count += len(rows)
            page.scans.append(MaterialSourceScan('rss_snapshot', MaterialKind.WEB_PAGE,
                request.published_start, request.published_end, 'queried', received_count=feed.received_count,
                inspected_count=len(rows), collection_id=feed.url, date_filter_basis='local_publication_metadata',
                has_more=True if len(feed.items) > len(rows) or 'rss_entry_limit' in feed.warnings else None,
                fetched_at=feed.fetched_at, discovery_provider='rss'))
            page.diagnostics.extend(_diag(v) for v in feed.warnings)
            for row in rows:
                context.check_active()
                if row.url in seen:
                    page.diagnostics.append(_diag('rss_duplicate_url'))
                    continue
                seen.add(row.url)
                published = row.published_at.date() if row.published_at else None
                if published and request.published_start and not request.published_start <= published <= request.published_end:
                    continue
                # Explicit keywords can avoid unnecessary original-page reads.
                if request.keywords and not any(v.casefold() in (row.title+'\n'+row.excerpt).casefold() for v in request.keywords):
                    continue
                text, scope = row.excerpt[:request.max_chars], TextScope.SOURCE_EXCERPT if row.excerpt else TextScope.METADATA
                title, original, publisher = row.title, row.url, urlsplit(feed.url).hostname
                source_ref = feed.url + '#entry-' + hashlib.sha256(row.url.encode()).hexdigest()[:20]
                tier, authority, evidence, kind = SourceTier.MEDIA, SourceAuthority.DISCOVERY, EvidenceType.UNKNOWN, MaterialKind.WEB_PAGE
                warnings = list(row.warnings) + ['rss_feed_metadata_not_article_verification']
                details = {'feed_url': feed.url, 'feed_title': row.title, 'feed_published_at': row.published_at.isoformat() if row.published_at else None,
                           'date_field': row.date_field, 'publication_basis': 'feed_metadata',
                           'feed_fetched_at': feed.fetched_at.isoformat(), 'content_state': 'feed_excerpt',
                           'feed_excerpt_not_article_body': True, 'source_text_trust': 'untrusted'}
                fetched_at, links, text_provider = feed.fetched_at, (), 'rss' if text else None
                if len(row.excerpt) > request.max_chars:
                    warnings.append('text_truncated')
                if reads < request.text_reads_per_source:
                    reads += 1
                    try:
                        document = self._reader(row.url, context=context, max_chars=request.max_chars, mode=request.web_read_mode)
                        state = document.extra.get('web_read', {}).get('content_state')
                        details['article_read'] = document.extra.get('web_read', {})
                        if document.errors or not document.text.strip() or state not in _VALID:
                            warnings.append('rss_original_text_unavailable')
                            page.diagnostics.append(_diag('rss_original_text_unavailable'))
                        else:
                            from .web_materials import _classification
                            from ir_search.institutions import _institution_for_url
                            _, host, _ = _url(document.url)
                            title, original, publisher = document.title[:1000], document.url, host
                            source_ref = row.url  # Distinct discovered links may redirect to the same original.
                            text, scope, fetched_at = document.text, TextScope.EXTRACTED_TEXT, document.fetched_at
                            text_provider = 'web'
                            warnings = [v for v in warnings if v != 'text_truncated']
                            warnings.extend(v for v in document.warnings if re.fullmatch(r'[a-z][a-z0-9_]{0,63}', v))
                            if any(v.startswith('text truncated to max_chars=') for v in document.warnings):
                                warnings.append('text_truncated')
                            kind, evidence, tier = _classification(host, title, True)
                            institution = _institution_for_url(original)
                            authority = SourceAuthority.REGULATOR if institution and institution.category == 'regulator' else SourceAuthority.COMPANY if institution else SourceAuthority.UNKNOWN
                            details.update(content_state=state, feed_excerpt_not_article_body=False)
                            links = tuple(document.extra.get('public_links', []))
                            if document.published_at:
                                if published and published != document.published_at.date():
                                    warnings.append('publication_date_conflict')
                                published = document.published_at.date()
                                instant = document.published_at if document.published_at.utcoffset() is not None else None
                                details['publication_basis'] = 'article_metadata'
                                warnings = [v for v in warnings if v != 'publication_date_unknown']
                            else:
                                instant = row.published_at if row.published_at and row.published_at.utcoffset() is not None else None
                    except DataAdapterError as exc:
                        warnings.append('rss_original_' + exc.code)
                        page.diagnostics.append(_diag(exc.code, exc.failure_kind))
                if scope != TextScope.EXTRACTED_TEXT:
                    instant = row.published_at if row.published_at and row.published_at.utcoffset() is not None else None
                    warnings.append('rss_feed_excerpt_not_article_body')
                if published and request.published_start and not request.published_start <= published <= request.published_end:
                    page.diagnostics.append(_diag('rss_article_outside_publication_window'))
                    continue
                page.candidates.append(MaterialCandidate(source_ref, title, kind, 'feed',
                    Provenance(self.name, publisher, fetched_at, authority=authority, source_tier=tier,
                               evidence_type=evidence, adapter_mode=AdapterMode.LIVE),
                    text=text, text_scope=scope, original_url=original, published_on=published, published_at=instant,
                    warnings=tuple(dict.fromkeys(warnings)), discovery_provider='rss', text_provider=text_provider,
                    collection_id=feed.url, source_record_type='rss_entry', links=links, read_details=details))
        page.diagnostics = list(dict.fromkeys(page.diagnostics))
        return page
