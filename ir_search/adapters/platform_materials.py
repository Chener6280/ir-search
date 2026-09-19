"""Scoped discovery for community posts and video, independent of generic web search."""
from __future__ import annotations

from ir_search.contracts import AdapterMode, Diagnostic, Provenance
from ir_search.contracts.materials import MaterialCandidate, MaterialCapability, MaterialKind, MaterialSearchPage, MaterialSourceScan, TextScope
from ir_search.infrastructure.credentials import WebMaterialProfile, SourceConfigError, web_material_profile, read_credentials
from ir_search.infrastructure.platform_documents import _platform_url
from ir_search.infrastructure.community_documents import read_community_document
from ir_search.infrastructure.video_documents import read_video_document
from ir_search.infrastructure.audio_documents import read_audio_document
from ir_search.infrastructure.public_web import _allowed_domain
from ir_search.infrastructure.recovery import _stop_source
from ir_search.infrastructure.web_search import BochaWebClient, AnySearchWebClient
from ir_search.models import SourceTier, SourceAuthority, EvidenceType
from ir_search.registry import DataAdapterError
from .web_materials import _publication, _field

_DOMAINS = {'xiaoyuzhou': 'xiaoyuzhoufm.com', 'xueqiu': 'xueqiu.com', 'eastmoney': 'guba.eastmoney.com', 'bilibili': 'bilibili.com', 'youtube': 'youtube.com'}


def platform_material_profile(provider, *, values=None, env_file=None):
    """Opt in separately, sharing only configured search API credentials."""
    if provider not in {'xueqiu', 'eastmoney', 'video', 'xiaoyuzhou'}: raise ValueError('Unknown platform provider')
    values = read_credentials(env_file) if values is None else values
    enabled = values.get(provider.upper() + '_MATERIALS_ENABLED', 'false').lower()
    if enabled not in {'true', 'false'}: raise SourceConfigError()
    if enabled == 'false': return None
    if provider != 'video' and not values.get('BOCHA_API_KEY'):
        raise SourceConfigError('source_credentials_missing')
    return web_material_profile(values={**values, 'WEB_MATERIALS_ENABLED': 'true', 'WEB_SEARCH_PROVIDER': 'regional'})


def _platform_plan(provider, request, profile):
    platforms = request.video_platforms if provider == 'video' else (provider,)
    limit = min(10, request.candidates_per_source)
    queries = []
    for index, platform in enumerate(platforms):
        domain = _DOMAINS[platform]
        terms = list(dict.fromkeys((request.question,) + request.entities + request.symbols + request.keywords))
        query = ' '.join(terms)[:700]
        if platform == 'youtube':
            query += ' site:youtube.com/watch'
        # Bocha has a native include filter; combining it with a literal site:
        # expression degraded platform detail recall in bounded live probes.
        # Date bounds are enforced locally. Appended numeric date ranges degraded
        # detail-page discovery in live probes and are not a provider date filter.
        queries.append({'platform': platform, 'discovery_provider': 'anysearch' if platform == 'youtube' else 'bocha',
            'web_region': 'overseas' if platform == 'youtube' else 'cn', 'query': query, 'domain': domain,
            'scope_allowed': _allowed_domain(domain, profile.allowed_domains),
            'max_candidates': limit // len(platforms) + int(index < limit % len(platforms)),
            'max_text_reads': request.text_reads_per_source // len(platforms) + int(index < request.text_reads_per_source % len(platforms))})
    return {'provider': provider, 'queries': queries, 'date_filter_basis': 'local_publication_metadata',
            'discovery_completeness': 'bounded_search_index_not_platform_archive', 'max_candidates': limit,
            'max_text_reads': request.text_reads_per_source, 'network_attempts_upper_bound': None,
            'video_languages': list(request.video_languages) if provider == 'video' else [],
            'audio_transcription': 'never_during_search',
            'cost_estimate': None, 'cost_basis': 'existing_search_provider_pricing_not_queried'}


class _PlatformMaterialAdapter:
    name = ''

    def __init__(self, profile, *, client=None, bocha_client=None, reader=None):
        if not isinstance(profile, WebMaterialProfile): raise ValueError('WebMaterialProfile required')
        self._profile = profile
        self._client = client or AnySearchWebClient(profile)
        self._bocha_client = bocha_client or BochaWebClient(profile)
        self._reader = reader or (read_audio_document if self.name == 'xiaoyuzhou' else read_video_document if self.name == 'video' else read_community_document)
        self._kind = MaterialKind.AUDIO if self.name == 'xiaoyuzhou' else MaterialKind.VIDEO if self.name == 'video' else MaterialKind.SOCIAL_POST
        self.capability = MaterialCapability(self.name, 'audio' if self.name == 'xiaoyuzhou' else 'video' if self.name == 'video' else 'community', (self._kind,),
            supports_publication_filter=False, search_basis='scoped_web_discovery_then_platform_read', coverage_notes=(
                'CN platforms use Bocha; YouTube uses AnySearch. Platform domains and detail URLs are enforced locally.',
                'Bounded indexed discovery is not exhaustive; no author timeline or complete archive is promised.',
                'Community posts and video remain UGC/opinion; reposts do not establish independent corroboration.',
                'During search, snippets, metadata and captions are distinct; no audio/video downloads or ASR.',
                'Xueqiu and Bilibili may require valid local session cookies; challenges and missing captions remain explicit.',
                'Xiaoyuzhou search reads show notes only; explicit retrieve(audio_mode=transcribe) is required for bounded ASR.'))

    def search_materials(self, request, *, context):
        """Discover detail URLs and preserve independent source/read failures."""
        if (not request.published_start or context.account_scope != self.capability.account_scope
                or request.material_types and self._kind not in request.material_types):
            raise DataAdapterError('unsupported')
        page = MaterialSearchPage([], 0)
        def diagnostic(code, failure=None):
            page.diagnostics.append(Diagnostic(code, 'search_materials', self.name, adapter_mode=AdapterMode.LIVE,
                **({'failure_kind': failure} if failure is not None else {})))
        diagnostic('bounded_platform_discovery')
        seen = set()
        for plan in _platform_plan(self.name, request, self._profile)['queries']:
            context.check_active()
            engine, platform = plan['discovery_provider'], plan['platform']
            scan = dict(operation=platform + '_discovery', material_type=self._kind, query_start=request.published_start,
                        query_end=request.published_end, discovery_provider=engine, web_region=plan['web_region'],
                        date_filter_basis='local_publication_metadata', routing_basis='fixed_platform_region', search_query=plan['query'])
            try:
                if not plan['scope_allowed']: raise DataAdapterError('blocked_url')
                if not plan['max_candidates']:
                    page.scans.append(MaterialSourceScan(**scan, state='candidate_budget_exhausted'))
                    continue
                client = self._bocha_client if engine == 'bocha' else self._client
                options = {'allowed_domains': (plan['domain'],)} if isinstance(client, BochaWebClient) else {}
                found = client.search(plan['query'], limit=plan['max_candidates'], context=context, **options)
                if not isinstance(found.rows, list) or len(found.rows) > 100 or any(not isinstance(r, dict) for r in found.rows):
                    raise DataAdapterError('upstream_schema')
            except DataAdapterError as exc:
                diagnostic(exc.code, exc.failure_kind)
                page.scans.append(MaterialSourceScan(**scan, state=exc.code))
                continue
            rows = found.rows[:plan['max_candidates']]
            page.scanned_count += len(rows)
            page.scans.append(MaterialSourceScan(**scan, state='queried', received_count=len(found.rows), inspected_count=len(rows), fetched_at=found.fetched_at))
            reads = 0
            read_blocker = None
            for row in rows:
                context.check_active()
                try:
                    actual, url, _ = _platform_url(_field(row, 'url', required=True))
                    if actual != platform: raise DataAdapterError('blocked_url')
                    if url in seen: continue
                    seen.add(url)
                    title, snippet = _field(row, 'title', required=True), _field(row, 'snippet')
                    text = snippet[:request.max_chars]
                    scope = TextScope.SEARCH_SNIPPET if text else TextScope.METADATA
                    warnings = ['community_content_not_verified', 'discovery_not_exhaustive', 'business_period_unknown']
                    if text: warnings.append('snippet_origin_unverified')
                    published, instant = None, None
                    try: published, instant = _publication(row.get('published_at'))
                    except ValueError: warnings.append('search_publication_invalid')
                    if published: warnings.append('publication_from_search_metadata')
                    publisher, fetched_at, details = 'unknown', found.fetched_at, {'platform': platform}
                    if reads < plan['max_text_reads'] and read_blocker is None:
                        reads += 1
                        try:
                            options = {'languages': request.video_languages} if self.name == 'video' else {}
                            doc = self._reader(url, context=context, max_chars=request.max_chars, **options)
                            context.check_active()
                            if (_platform_url(doc.url)[1] != url or doc.extra.get('adapter_mode') != 'live' or doc.errors
                                    or doc.extra.get('generated')): raise DataAdapterError('upstream_schema')
                            details = doc.extra.get('web_read', {})
                            if details.get('content_state') not in {'article_text', 'metadata_only'}:
                                raise DataAdapterError('upstream_schema')
                            title = doc.title if doc.title != url else title
                            publisher, fetched_at = doc.extra.get('publisher', 'unknown'), doc.fetched_at
                            warnings.extend(doc.warnings)
                            if doc.text:
                                text, scope = doc.text[:request.max_chars], (TextScope.SOURCE_EXCERPT if self.name == 'xiaoyuzhou' else TextScope.EXTRACTED_TEXT)
                                warnings = [w for w in warnings if w != 'snippet_origin_unverified']
                            elif self.name not in {'video', 'xiaoyuzhou'}: raise DataAdapterError('no_extracted_text')
                            else:
                                diagnostic(details.get('caption_status', 'audio_show_notes_unavailable' if self.name == 'xiaoyuzhou' else 'video_captions_unavailable'))
                            if doc.published_at:
                                actual_day, actual_time = _publication(doc.published_at)
                                if published and actual_day != published: warnings.append('publication_date_conflict')
                                published, instant = actual_day, actual_time
                                warnings = [w for w in warnings if w != 'publication_from_search_metadata']
                        except DataAdapterError as exc:
                            diagnostic(exc.code, exc.failure_kind)
                            details = {'platform': platform, 'read_failure': exc.code}
                            warnings.append('original_text_fetch_failed')
                            if _stop_source(exc.code): read_blocker = exc.code
                    elif read_blocker is not None and reads < plan['max_text_reads']:
                        # A challenge/auth/rate failure stops body reads for this
                        # platform only; discovered snippets remain explicit.
                        details.update(prior_read_failure=read_blocker)
                        warnings.append('text_not_read_after_source_failure')
                    else:
                        warnings.append('text_not_read_within_budget')
                        diagnostic('text_read_budget_exhausted')
                    if published and not request.published_start <= published <= request.published_end:
                        diagnostic('platform_outside_publication_window')
                        continue
                    if not published: warnings.append('published_date_unknown')
                    page.candidates.append(MaterialCandidate(url, title, self._kind, self.capability.channel,
                        Provenance(self.name, publisher, fetched_at, source_tier=SourceTier.UGC, authority=SourceAuthority.UGC,
                                   evidence_type=EvidenceType.OPINION, adapter_mode=AdapterMode.LIVE),
                        text=text, text_scope=scope, original_url=url, published_on=published, published_at=instant,
                        warnings=tuple(dict.fromkeys(warnings)), authors=(publisher,) if publisher != 'unknown' else (),
                        discovery_provider=engine, read_details=details))
                except DataAdapterError as exc:
                    if exc.code in {'unsupported', 'blocked_url'}:
                        diagnostic('platform_result_not_detail')
                    else:
                        diagnostic(exc.code, exc.failure_kind)
                except (ValueError, TypeError, AttributeError): diagnostic('upstream_schema', DataAdapterError.KINDS['upstream_schema'])
        page.diagnostics = list(dict.fromkeys(page.diagnostics))
        return page


class XueqiuMaterialAdapter(_PlatformMaterialAdapter):
    """Xueqiu post discovery and original article extraction."""
    name = 'xueqiu'


class EastmoneyMaterialAdapter(_PlatformMaterialAdapter):
    """Eastmoney Guba post materials, separate from market-data services."""
    name = 'eastmoney'


class VideoMaterialAdapter(_PlatformMaterialAdapter):
    """Bilibili and YouTube discovery, metadata and available captions."""
    name = 'video'


class XiaoyuzhouMaterialAdapter(_PlatformMaterialAdapter):
    """Public podcast discovery and show notes; never triggers ASR in search."""
    name = 'xiaoyuzhou'
