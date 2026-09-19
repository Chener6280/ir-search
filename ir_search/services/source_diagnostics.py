"""Configuration, dependency and explicit live checks without legacy imports."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.util import find_spec
import argparse
import json

from ir_search.context import RequestContext, RequestStopped
from ir_search.infrastructure.credentials import source_configuration_status, SourceConfigError
from ir_search.infrastructure.recovery import _recovery
from ir_search.registry import DataAdapterError


@dataclass(frozen=True)
class _Source:
    operations: tuple[str, ...]
    backends: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    optional_dependencies: tuple[str, ...] = ()
    scope: str = 'bounded_provider_results'


_M = ('search_materials', 'retrieve')
_D = ('get_data',)
_SOURCES = {
    'wind_mysql': _Source(_D, ('mysql',), ('pymysql',)),
    'jydb': _Source(_D+_M, ('mysql',), ('pymysql',)),
    'akshare': _Source(_D, ('configured_akshare_backend',), ('akshare',)),
    'fmp': _Source(_D, ('fmp_stable_api',)),
    'tushare_corpus': _Source(_M, ('official_mcp',)),
    'web': _Source(_M, ('regional_search', 'public_http', 'optional_crawl4ai', 'explicit_scrapling_parser', 'explicit_firecrawl'), optional_dependencies=('crawl4ai','scrapling')),
    'rss': _Source(_M, ('public_rss_atom', 'explicit_original_page_read'), scope='configured_feed_snapshots_not_full_history'),
    'zsxq': _Source(_M, ('official_mcp',), scope='authorized_group_timelines_and_topics'),
    'wechat': _Source(_M, ('dajiala_discovery', 'body_cache', 'public_http', 'optional_crawl4ai', 'dajiala_body'),
                      optional_dependencies=('crawl4ai',), scope='configured_account_timelines'),
    'ima': _Source(_M, ('official_openapi',), scope='authorized_knowledge_bases_and_notes'),
    'wisburg': _Source(_M, ('official_mcp',), scope='stored_summaries_articles_commentary'),
    'alphapai': _Source(_M, ('account_browser',), ('playwright',), scope='shared_meetings'),
    'gangtise': _Source(_M, ('account_http_session',), optional_dependencies=('playwright',)),
    'xhs': _Source(_M, ('local_xiaohongshu_mcp_http',), scope='one_search_snapshot_and_discovered_notes'),
    'xueqiu': _Source(_M, ('bocha_discovery', 'selected_http_or_browser'), optional_dependencies=('playwright',), scope='bounded_public_posts'),
    'eastmoney': _Source(_M, ('bocha_discovery', 'platform_http'), scope='guba_posts_not_market_data'),
    'video': _Source(_M, ('regional_search', 'platform_metadata', 'available_captions'),
                     optional_dependencies=('youtube_transcript_api',), scope='metadata_and_existing_captions_not_transcription'),
    'xiaoyuzhou': _Source(_M, ('bocha_discovery', 'public_metadata', 'explicit_volcengine_asr'),
                          optional_dependencies=('websockets',), scope='metadata_or_explicit_bounded_audio_windows'),
    'sec': _Source(_M, ('sec_edgar',), scope='company_scoped_original_filings'),
    'fiona': _Source(_D, ('official_mcp',), scope='bounded_derivatives_quotes_bars_and_native_risk'),
    'global_macro': _Source(_D, ('official_public_series',), scope='native_frequency_latest_revised_snapshot'),
    'hkex': _Source(_M, ('official_title_search', 'public_http'), scope='active_hk_securities_original_disclosures'),
    'company_ir': _Source(_M, ('reviewed_issuer_directories', 'public_http', 'optional_crawl4ai'), optional_dependencies=('crawl4ai',), scope='curated_issuer_directory_snapshots'),
}


def _dependency(name):
    try: installed = find_spec(name) is not None
    except (ImportError, ValueError, AttributeError): installed = False
    extras = {'pymysql':'mysql', 'akshare':'akshare', 'playwright':'browser', 'crawl4ai':'crawl',
              'youtube_transcript_api':'video', 'websockets':'audio', 'scrapling':'scrapling'}
    return {'name': name, 'installed': installed, 'runtime_verified': False,
            'installation_extra': extras.get(name)}


def diagnose_sources(providers=(), *, env_file=None, live=False, context=None):
    """Report current local readiness; live=True requires explicitly named sources.

    Only XHS's existing read-only login probe is currently supported. Other live
    checks remain not_probed, never become green by configuration alone. No data
    queries, package installs, cookie discovery or automatic login are performed.
    """
    if (type(live) is not bool or not isinstance(providers, (list, tuple))
            or any(not isinstance(p, str) or p not in _SOURCES for p in providers)
            or len(set(providers)) != len(providers) or live and not providers):
        raise ValueError('Use known, explicit providers for live checks')
    context = context or RequestContext(timeout_seconds=30, max_operations=10)
    configuration = source_configuration_status(env_file=env_file)
    selected = tuple(providers) or tuple(_SOURCES)
    rows = {s['provider']: s for s in configuration['sources']}
    report = {'schema_version': '1.0', 'checked_at': datetime.now(timezone.utc).isoformat(),
        'verification_basis': 'explicit_live_probe_and_local_metadata' if live else 'local_metadata_only',
        'source_calls_started': 0, 'sources': [], 'diagnostics': configuration['diagnostics'],
        'automatic_configuration_changes': False}
    for provider in selected:
        spec = _SOURCES[provider]; configured = rows.get(provider, {})
        required = [_dependency(name) for name in spec.dependencies]
        optional = [_dependency(name) for name in spec.optional_dependencies]
        if provider == 'xueqiu' and configured.get('read_mode') == 'browser':
            required, optional = [_dependency('playwright')], []
        code = configured.get('code')
        state = ('configuration_error' if code or not configured else
                 'disabled' if not configured.get('enabled') else
                 'dependency_missing' if any(not d['installed'] for d in required) else
                 'configured_unverified')
        if state == 'configuration_error': code = 'source_config_error'
        elif state == 'disabled': code = 'source_disabled'
        elif state == 'dependency_missing': code = 'dependency_missing'
        item = {'provider': provider, 'state': state, 'configured': bool(configured.get('configured')),
            'operations': list(spec.operations), 'backend_chain': list(spec.backends),
            'backend_chain_basis': 'declared_routes_not_live_selection_or_automatic_failover',
            'active_backend': None, 'required_dependencies': required, 'optional_dependencies': optional,
            'scope': spec.scope, 'live_probe': {'state': 'not_requested', 'scope': None},
            'search_live_verified': False, 'retrieve_live_verified': False, 'get_data_live_verified': False,
            'diagnostics': []}
        if code: item['diagnostics'].append({'code': code, **_recovery(code)})
        if provider == 'xueqiu':
            item['reader_configuration'] = {key: configured[key] for key in (
                'read_mode', 'browser_cookie_mode', 'browser_channel', 'browser_headless',
                'browser_experimental', 'browser_runtime_verified') if key in configured}
        if provider in {'web', 'rss'}:
            from ir_search.infrastructure.web_toolkit import web_toolkit_status
            item['reader_configuration'] = web_toolkit_status(env_file=env_file)
        if live:
            item['live_probe']['state'] = 'not_probed'
            if state == 'configured_unverified' and provider == 'xhs':
                try:
                    context.check_active()
                    from ir_search.infrastructure.xhs import XhsClient, xhs_profile
                    profile = xhs_profile(env_file=env_file)
                    if profile is None: raise DataAdapterError('source_disabled')
                    report['source_calls_started'] += 1
                    XhsClient(profile).check_login(context=context)
                    item.update(state='login_verified', active_backend=spec.backends[0])
                    item['live_probe'] = {'state': 'passed', 'scope': 'login_status_only_not_search_or_body'}
                except (DataAdapterError, RequestStopped, SourceConfigError) as exc:
                    item['state'] = 'probe_failed'
                    item['live_probe'] = {'state': 'failed', 'scope': 'login_status_only_not_search_or_body'}
                    item['diagnostics'].append({'code': exc.code, **_recovery(exc.code)})
                except Exception:
                    item['state'] = 'probe_failed'
                    item['live_probe']['state'] = 'failed'
                    item['diagnostics'].append({'code': 'probe_failed', **_recovery('upstream_schema')})
            elif state == 'configured_unverified':
                item['diagnostics'].append({'code': 'live_probe_not_implemented',
                    'next_action': '使用明确范围的 get_data/search_materials/retrieve 样本另行验收。', 'automatic_retry': False})
        report['sources'].append(item)
    report['operations_used'] = context.operations
    report['status'] = 'partial' if report['diagnostics'] or any(
        s['state'] in {'configuration_error', 'dependency_missing', 'probe_failed'} for s in report['sources']) else 'ok'
    report['status_basis'] = 'diagnostic_report_completed_not_all_sources_live_verified'
    return report


def main(argv=None):
    """CLI equivalent of diagnose_sources; defaults to zero network calls."""
    parser = argparse.ArgumentParser(description='Inspect ir-search sources without exposing credentials')
    parser.add_argument('--provider', action='append', default=[], choices=sorted(_SOURCES))
    parser.add_argument('--live', action='store_true', help='Explicit read-only probe; requires --provider')
    parser.add_argument('--timeout', type=float, default=30)
    args = parser.parse_args(argv)
    if args.live and not args.provider: parser.error('--live requires --provider')
    try: result = diagnose_sources(args.provider, live=args.live, context=RequestContext(timeout_seconds=args.timeout))
    except ValueError: parser.error('Invalid timeout or provider options')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'ok' else 1


if __name__ == '__main__': raise SystemExit(main())
