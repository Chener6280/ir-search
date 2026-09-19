"""Pure web planning shared by execution and dry-run; no provider calls."""
from __future__ import annotations

from ir_search.contracts.materials import MaterialKind
from ir_search.entity import load_entities
from ir_search.institutions import _selected_institutions, _host_matches
from .web_routing import route_web_search


def _effective_domains(request, profile):
    scoped = tuple(dict.fromkeys(d for row in _selected_institutions(request.web_institutions) for d in row.domains))
    configured = profile.allowed_domains
    if not scoped or not configured:
        return scoped or configured, False
    # Intersection, never relax a configured host restriction.
    domains = tuple(dict.fromkeys(d for d in (*scoped, *configured)
        if _host_matches(d, scoped) and _host_matches(d, configured)))
    return domains, not bool(domains)


def _web_query(request, domains):
    terms = [request.question, *request.keywords]
    if request.symbols:
        names = [entity.names[0] for entity in load_entities() if entity.names and set(entity.codes) & set(request.symbols)]
        terms.extend(names or request.symbols)
    if request.material_types == (MaterialKind.POLICY,):
        terms.append('政策 通知')
    if request.published_start:
        terms.append(f'{request.published_start.isoformat()} {request.published_end.isoformat()}')
    if domains:
        terms.append('(' + ' OR '.join('site:' + d for d in domains) + ')')
    return ' '.join(dict.fromkeys(terms))


def _web_plan(request, profile):
    domains, conflict = _effective_domains(request, profile)
    routes = route_web_search(request, profile)
    budget = min(request.candidates_per_source, 10)
    planned = []
    for index, route in enumerate(routes):
        candidates = budget // len(routes) + (index < budget % len(routes))
        reads = request.text_reads_per_source // len(routes) + (index < request.text_reads_per_source % len(routes))
        planned.append({'provider': route.provider, 'region': route.region, 'routing_basis': route.basis,
                        'query': _web_query(request, domains), 'max_candidates': candidates,
                        'domain_filter': 'api_include_and_local_check' if route.provider in {'bocha', 'exa'} and domains else 'query_hint_and_local_check' if domains else 'unrestricted_public_web',
                        'max_text_reads': min(reads, candidates)})
    return {'allowed_domains': list(domains), 'scope_conflict': conflict, 'routes': planned,
            'max_candidates': budget, 'max_text_reads': sum(r['max_text_reads'] for r in planned),
            'max_discovery_calls': 0 if conflict else sum(bool(r['max_candidates']) for r in planned),
            'read_workers': request.web_read_workers if request.web_read_mode == 'http' else 1,
            'read_workers_requested': request.web_read_workers, 'read_mode': request.web_read_mode,
            'concurrency_policy': 'http_only_browser_capable_reads_remain_serial',
            'min_http_start_interval_per_host_seconds': 0.25,
            'rate_limit_policy': 'stop_host_for_this_request_after_http_429_no_retry',
            'quota_fallback': {'on': ['quota'], 'target': 'caller_native_web_search',
                'execution_owner': 'caller', 'result_field': 'fallback_requests',
                'max_attempts_per_failed_route': 1, 'server_executes_fallback': False,
                'rate_limit_is_quota': False, 'after_search': 'retrieve'},
            'publication_filter': 'local_metadata_unknown_dates_retained_with_warning',
            'attachment_downloads': 0, 'query_dates_are_hints': True,
            'directory_seeds': [url for row in _selected_institutions(request.web_institutions) for url in row.directory_urls],
            'directory_seed_policy': 'reference_only_use_retrieve_explicitly',
            'network_attempts_upper_bound': None,
            'network_budget_note': 'Redirects and optional browser requests consume the shared operation budget; reads are not HTTP call counts'}
