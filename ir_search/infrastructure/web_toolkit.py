"""Explicit, optional web readers. No automatic cloud billing or AI extraction."""
from __future__ import annotations

from dataclasses import dataclass, field
from importlib.util import find_spec
import json

from ir_search.infrastructure.credentials import read_credentials, SourceConfigError
from ir_search.registry import DataAdapterError

WEB_READ_MODES = ('http', 'auto', 'browser', 'scrapling', 'firecrawl')


@dataclass(frozen=True)
class FirecrawlProfile:
    api_key: str = field(repr=False)

    def __post_init__(self):
        if (not isinstance(self.api_key, str) or not 1 <= len(self.api_key) <= 4096
                or any(ord(c) < 33 or ord(c) > 126 for c in self.api_key)):
            raise SourceConfigError()


def firecrawl_profile(*, values=None, env_file=None):
    """Require both configuration opt-in and a key; never expose the key in repr."""
    values = read_credentials(env_file) if values is None else values
    enabled = values.get('FIRECRAWL_ENABLED', 'false').lower()
    if enabled not in {'true', 'false'}:
        raise SourceConfigError()
    if enabled == 'false':
        return None
    return FirecrawlProfile(values.get('FIRECRAWL_API_KEY', ''))


def web_toolkit_status(*, env_file=None):
    """Local metadata only. Dependency/key presence does not verify source access."""
    try:
        configured = firecrawl_profile(env_file=env_file) is not None
        cloud = {'configured': configured, 'state': 'configured_unverified' if configured else 'disabled'}
    except SourceConfigError:
        cloud = {'configured': False, 'state': 'configuration_error'}
    try:
        installed = find_spec('scrapling') is not None
    except (ValueError, ImportError):
        installed = False
    return {'verification_basis': 'local_metadata_only', 'default_changed': False,
            'scrapling': {'installed': installed, 'installation_extra': 'scrapling',
                         'scope': 'html_parser_only_no_stealth_fetcher', 'live_verified': False},
            'firecrawl': {**cloud, 'explicit_mode_required': True, 'may_incure_provider_charges': True,
                          'formats': ['rawHtml'], 'llm_extraction': False, 'live_verified': False}}


def _scrapling_selector():
    try:
        from scrapling import Selector
        return Selector
    except (ImportError, OSError):
        raise DataAdapterError('dependency_missing') from None


def extract_scrapling_document(raw: bytes, url: str, *, max_chars=20000):
    """Select an unambiguous semantic body, retaining full-page source metadata.

    No fuzzy/adaptive recovery: changed/ambiguous markup returns the stdlib text
    with explicit diagnostics. Challenge and directory pages are never pruned.
    """
    if type(max_chars) is not int or not 1 <= max_chars <= 100000:
        raise ValueError('Invalid text limit')
    if not isinstance(raw, bytes) or len(raw) > 8 * 1024 * 1024:
        raise DataAdapterError('response_too_large')
    selector_cls = _scrapling_selector()
    from ir_search.documents.html import extract_html_document
    from ir_search.documents.models import hash_text, make_doc_id
    from .web_documents import _state, _links, WebContentState
    document = extract_html_document(raw, url, max_chars=100000)
    details = {'requested': 'scrapling', 'selected': 'stdlib', 'adaptive_matching': False,
               'completeness': 'not_verified', 'state': 'ineligible_content_state'}
    if _state(document, _links(document)[0]) == WebContentState.ARTICLE:
        try:
            root = selector_cls(raw.decode('utf-8'), url=url, adaptive=False)
            # A multi-article page is a listing, not permission to select article 1.
            articles = root.css('article')
            nodes = articles if articles else root.css('main, [role="main"]')
            details.update(selector='article' if articles else 'main, [role="main"]', matches=len(nodes))
            details['state'] = 'ambiguous_or_missing_body'
            if len(nodes) == 1:
                text = str(nodes[0].get_all_text(separator='\n\n', strip=True,
                           ignore_tags=('script', 'style', 'noscript', 'svg'), valid_values=True)).strip()
                if len(text) >= 80:
                    document.text = text
                    document.extraction_method = 'scrapling_semantic_body'
                    details.update(selected='scrapling', state='selected')
                else:
                    details['state'] = 'body_too_short'
        except Exception:
            # A parser failure does not erase the ordinary parser's usable result.
            details['state'] = 'parser_error'
    if details['selected'] == 'stdlib':
        document.warnings.append('scrapling_selection_fallback')
    base_truncated = any(v.startswith('text truncated to max_chars=') for v in document.warnings)
    document.warnings = [v for v in document.warnings if not v.startswith('text truncated to max_chars=')]
    if len(document.text) > max_chars or (details['selected'] == 'stdlib' and base_truncated):
        document.warnings.append('text_truncated')
    document.text = document.text[:max_chars]
    document.text_hash = hash_text(document.text)
    document.doc_id = make_doc_id(document.url, document.text_hash)
    document.extra['html_extraction'] = details
    return document


def fetch_firecrawl_document(url, *, context, max_chars=20000, allowed_domains=(), profile=None):
    """One explicit cloud scrape of a public HTML URL; no keys/cookies sent to origin.

    The remote provider controls redirects and DNS. Locally validate the initial
    address and returned address, and report that intermediate hops are unobserved.
    """
    from .public_web import _url, _allowed_domain, _request, _resolve
    from ir_search.documents.html import extract_html_document
    if type(max_chars) is not int or not 1 <= max_chars <= 100000:
        raise ValueError('Invalid text limit')
    if not isinstance(allowed_domains, tuple) or any(not isinstance(v, str) or not v for v in allowed_domains):
        raise ValueError('Invalid domain restriction')
    parsed, host, port = _url(url)
    if not _allowed_domain(host, allowed_domains) or parsed.query or parsed.fragment:
        # Cloud receives only plain public URLs, no signed links or query secrets.
        raise DataAdapterError('blocked_url')
    context.check_active()
    try:
        profile = profile or firecrawl_profile()
    except SourceConfigError:
        raise DataAdapterError('source_config_error') from None
    if profile is None:
        raise DataAdapterError('source_disabled')
    # This does not assert that remote DNS resolves identically to local DNS.
    try:
        _resolve(host, port, context)
    except OSError:
        raise DataAdapterError('network') from None
    payload = {'url': url, 'formats': ['rawHtml'], 'onlyMainContent': False,
               'maxAge': 0, 'storeInCache': False, 'skipTlsVerification': False,
               'parsers': [], 'proxy': 'basic', 'timeout': max(1, min(60000, int(context.remaining_seconds()*1000)))}
    reply = _request('https://api.firecrawl.dev/v2/scrape', context=context, method='POST',
                     body=json.dumps(payload).encode(), max_bytes=8 * 1024 * 1024,
                     headers={'Authorization': 'Bearer ' + profile.api_key, 'Content-Type': 'application/json',
                              'Accept': 'application/json'})
    if reply.status != 200:
        raise DataAdapterError('upstream_schema')  # Never follow API redirects with credentials.
    try:
        body = json.loads(reply.body)
        data = body['data']
        metadata = data['metadata']
        html = data['rawHtml']
        if body.get('success') is not True or not isinstance(html, str) or not html.strip():
            raise ValueError()
        status = metadata.get('statusCode')
        if type(status) is not int:
            raise ValueError()
        if status != 200:
            code = {401:'authentication_failed', 403:'entitlement_denied', 404:'not_found', 429:'rate_limit'}.get(status, 'network')
            raise DataAdapterError(code)
        final_url = metadata.get('url')
        if not isinstance(final_url, str) or not final_url:
            raise ValueError()  # sourceURL alone cannot establish the final location.
    except (KeyError, ValueError, TypeError, AttributeError, UnicodeError):
        raise DataAdapterError('upstream_schema') from None
    final, final_host, final_port = _url(final_url)
    if not _allowed_domain(final_host, allowed_domains) or (parsed.scheme == 'https' and final.scheme != 'https'):
        raise DataAdapterError('blocked_url')
    try:
        _resolve(final_host, final_port, context)
    except OSError:
        raise DataAdapterError('network') from None
    context.check_active()
    document = extract_html_document(html.encode(), final_url, max_chars=max_chars)
    document.fetched_at = reply.fetched_at
    document.extra.update(adapter_mode='live', requested_url=url,
        firecrawl={'format': 'rawHtml', 'llm_extraction': False, 'max_age_requested_ms': 0,
                   'cache_write_requested': False, 'proxy_requested': 'basic',
                   'redirect_scope': 'remote_intermediate_hops_unobserved',
                   'url_checks': 'local_initial_and_reported_final_only',
                   'raw_hash_scope': 'provider_returned_html_not_origin_wire_bytes',
                   'fetched_at_basis': 'client_response_receipt'})
    document.warnings.append('remote_fetch_redirects_unobserved')
    return document
