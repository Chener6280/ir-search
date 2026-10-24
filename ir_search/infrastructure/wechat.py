"""WeChat account discovery and article reads, independent of legacy research.

Credentials go only in POST bodies to the fixed vendor endpoint. Public WeChat
requests carry no credentials. No paid retries or alternative endpoints are hidden.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace, asdict
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
import json
import hashlib
import re
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ir_search.documents.models import Document, hash_text, make_doc_id, document_from_dict
from ir_search.models import EvidenceType, SourceTier
from ir_search.registry import DataAdapterError
from ir_search.context import RequestStopped
from .credentials import SourceConfigError, wechat_profile
from .public_web import _request
from .wechat_cache import _default_cache
from .web_browser import render_public_html

_CST = timezone(timedelta(hours=8))
_API = 'https://www.dajiala.com/fbmain/monitor/v3/'
_ARTICLE_CACHE = 'article_v3'


class _PhaseContext:
    """A shorter phase deadline that still charges the caller's operation budget."""
    def __init__(self, parent, seconds):
        self.parent, self.deadline = parent, time.monotonic() + seconds

    def remaining_seconds(self):
        return max(0.0, min(self.parent.remaining_seconds(), self.deadline - time.monotonic()))

    def check_active(self):
        self.parent.check_active()
        if time.monotonic() >= self.deadline: raise RequestStopped('deadline_exceeded')

    def begin_operation(self):
        self.check_active()
        self.parent.begin_operation()

    def _wait_for_public_host(self, host):
        self.check_active()
        ready = self.parent._wait_for_public_host(host)
        self.check_active()
        return ready

    def _mark_public_host_limited(self, host):
        self.parent._mark_public_host_limited(host)


class _BrowserPhase(_PhaseContext):
    """Reserve one vendor operation and some time after an optional browser read."""
    def __init__(self, parent):
        super().__init__(parent, min(18, parent.remaining_seconds() - 8))
        self.max_operations = min(parent.max_operations - 1, parent.operations + 35)

    @property
    def operations(self):
        return self.parent.operations

    @operations.setter
    def operations(self, value):
        self.parent.operations = value

    def begin_operation(self):
        if self.operations >= self.max_operations: raise RequestStopped('operation_budget_exhausted')
        super().begin_operation()


def normalize_wechat_url(url):
    """Accept only public article URLs; remove tracking while retaining identity."""
    try:
        if not isinstance(url, str) or len(url) > 8192 or any(c.isspace() or ord(c) < 32 for c in url) or '\\' in url: raise ValueError()
        parsed = urlsplit(unescape(url))
        if parsed.scheme not in {'http', 'https'} or parsed.netloc.lower() != 'mp.weixin.qq.com': raise ValueError()
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        if len({k for k, _ in pairs}) != len(pairs): raise ValueError()
        query = dict(pairs)
        if any(k.lower() in {'key','token','access_token','pass_ticket','wxtoken','uin','api_key','apikey','password','secret','authorization'} for k in query): raise ValueError()
        if re.fullmatch(r'/s/[A-Za-z0-9_-]{8,128}', parsed.path):
            return 'https://mp.weixin.qq.com' + parsed.path
        if parsed.path != '/s': raise ValueError()
        if not re.fullmatch(r'[A-Za-z0-9+/=]{4,160}', query.get('__biz','')): raise ValueError()
        if not re.fullmatch(r'\d{1,20}', query.get('mid','')) or not re.fullmatch(r'[1-8]',query.get('idx','1')): raise ValueError()
        if not re.fullmatch(r'[a-fA-F0-9]{32}',query.get('sn','')): raise ValueError()
        return urlunsplit(('https','mp.weixin.qq.com','/s',urlencode([(k,query.get(k,'1')) for k in ('__biz','mid','idx','sn')]),''))
    except (ValueError, TypeError, AttributeError):
        raise DataAdapterError('blocked_url') from None


def _published(row):
    value = row.get('post_time')
    try:
        if not isinstance(value, bool) and isinstance(value, (int, float, str)) and re.fullmatch(r'\d{10}', str(value)):
            return datetime.fromtimestamp(int(value), _CST)
        value = row.get('post_time_str')
        if isinstance(value, str) and re.fullmatch(r'\d{4}-\d\d-\d\d \d\d:\d\d:\d\d', value):
            return datetime.fromisoformat(value).replace(tzinfo=_CST)
    except (ValueError, OverflowError, OSError): pass
    return None


@dataclass(frozen=True)
class WechatHistoryPage:
    rows: tuple[dict, ...] = field(repr=False)
    publisher: str
    ghid: str
    next_cursor: str | None
    has_more: bool
    fetched_at: datetime
    cache_state: str = 'fresh'
    warnings: tuple[str, ...] = ()


class DajialaMaterialClient:
    """Bounded vendor API calls. Account-name matching requires one exact result."""
    def __init__(self, profile, *, transport=None, cache=None, cache_mode='use'):
        if cache_mode not in ('use', 'refresh', 'off'): raise ValueError('Invalid WeChat cache mode')
        self._profile, self._transport = profile, transport or _request
        self._cache = cache or _default_cache(profile.cache_dir)
        self.cache_mode = cache_mode
        self._heads = {}
        self._account_cache_hit = False
        self._resolved_in_request = {}

    def _call(self, operation, payload, context):
        if operation not in {'wx_account/search','post_history','article_html'}: raise DataAdapterError('unsupported')
        context.check_active()
        try:
            reply = self._transport(_API + operation, context=_PhaseContext(context, 20), method='POST',
                body=json.dumps(dict(payload, key=self._profile.api_key, verifycode=''), ensure_ascii=False).encode(),
                headers={'Content-Type':'application/json'}, max_bytes=8 * 1024 * 1024)
        except RequestStopped as exc:
            context.check_active()
            if exc.code == 'deadline_exceeded': raise DataAdapterError('timeout') from None
            raise
        context.check_active()
        if reply.status != 200: raise DataAdapterError('upstream_schema')
        try: result = json.loads(reply.body)
        except (ValueError, UnicodeError): raise DataAdapterError('upstream_schema') from None
        if not isinstance(result, dict) or type(result.get('code')) is not int: raise DataAdapterError('upstream_schema')
        code = result['code']
        if code != 0:
            safe = {10002:'authentication_failed',20001:'quota',-1:'rate_limit',111:'rate_limit',
                    101:'not_found',104:'not_found',105:'no_extracted_text',106:'no_extracted_text',107:'no_extracted_text',
                    2003:'network',2005:'network',50000:'network'}
            raise DataAdapterError(safe.get(code,'upstream_schema'))
        if result.get('mode') in {2023,'2023'}: raise DataAdapterError('entitlement_denied')
        return result, reply.fetched_at

    def resolve_account(self, account, *, context):
        """Resolve a configured name, never silently select a fuzzy account match."""
        context.check_active()
        self._account_cache_hit = False
        if account.ghid: return account.ghid
        memo = self._resolved_in_request.get(account.name)
        if self.cache_mode != 'use' and memo and memo[0] == context.request_id:
            self._account_cache_hit = True
            return memo[1]
        with self._cache._guard(context) if self.cache_mode != 'off' else nullcontext(False) as enabled:
            cached = self._cache._get('account', account.name, 7 * 86400) if enabled and self.cache_mode == 'use' else None
            if isinstance(cached, str) and re.fullmatch(r'gh_[A-Za-z0-9_-]{1,80}', cached):
                self._account_cache_hit = True
                return cached
            ghid = self._resolve_account(account, context)
            self._resolved_in_request[account.name] = (context.request_id, ghid)
            if enabled: self._cache._put('account', account.name, ghid)
            return ghid

    def _resolve_account(self, account, context):
        result, _ = self._call('wx_account/search', {'keyword':account.name,'page':'1','size':'5','mode':1}, context)
        rows = result.get('data')
        if not isinstance(rows,list) or len(rows)>5 or any(not isinstance(r,dict) for r in rows): raise DataAdapterError('upstream_schema')
        matches = [r for r in rows if r.get('name') == account.name]
        if len(matches) != 1: raise DataAdapterError('wechat_account_unresolved')
        ghid = matches[0].get('ghid')
        if not isinstance(ghid,str) or not re.fullmatch(r'gh_[A-Za-z0-9_-]{1,80}',ghid): raise DataAdapterError('upstream_schema')
        return ghid

    def history(self, account, *, cursor='', context):
        """Read one opaque-cursor page; do not infer coverage from page length."""
        if not isinstance(cursor,str) or len(cursor)>512 or any(ord(c)<32 for c in cursor): raise DataAdapterError('invalid_cursor')
        ghid = self.resolve_account(account, context=context)
        # Tail keys include the complete head snapshot. New/edited head rows or a
        # changed opaque cursor invalidate every old tail; never reuse raw offsets.
        generation = self._heads.get(ghid)
        key = ghid + ':' + (generation or 'unanchored') + ':' + cursor if cursor else ghid
        with self._cache._guard(context) if self.cache_mode != 'off' else nullcontext(False) as enabled:
            cached = self._cache._get('history', key, 86400 if cursor else 300) if enabled and self.cache_mode == 'use' and (not cursor or generation) else None
            page = None
            if isinstance(cached, dict):
                try:
                    page = WechatHistoryPage(tuple(cached['rows']), cached['publisher'], cached['ghid'], cached['next_cursor'],
                        cached['has_more'], datetime.fromisoformat(cached['fetched_at']), 'hit')
                    if (page.ghid != ghid or len(page.rows) > 1000 or any(not isinstance(r, dict) for r in page.rows)
                            or not isinstance(page.publisher, str) or not 1 <= len(page.publisher.strip()) <= 100
                            or type(page.has_more) is not bool or page.fetched_at.utcoffset() is None
                            or page.has_more and (not isinstance(page.next_cursor, str) or not 1 <= len(page.next_cursor) <= 512
                                or any(ord(c) < 32 for c in page.next_cursor))
                            or not page.has_more and page.next_cursor is not None): raise ValueError()
                except (KeyError, ValueError, TypeError):
                    self._cache.warning = 'wechat_cache_invalid'
                    page = None
            if page is None:
                page = self._history(ghid, cursor, context)
                if enabled and (not cursor or generation):
                    data = asdict(page)
                    data['fetched_at'] = page.fetched_at.isoformat()
                    self._cache._put('history', key, data)
            if not cursor:
                stable = json.dumps([page.rows, page.publisher, page.ghid, page.next_cursor, page.has_more], sort_keys=True, ensure_ascii=False)
                self._heads[ghid] = hashlib.sha256(stable.encode()).hexdigest()
            return replace(page, warnings=tuple(w for w in (
                'wechat_history_cache_hit' if page.cache_state == 'hit' else None,
                'wechat_account_cache_hit' if self._account_cache_hit else None,
                self._cache.warning if enabled or self.cache_mode != 'off' else None) if w))

    def _history(self, ghid, cursor, context):
        result, fetched = self._call('post_history', {'ghid':ghid,'url':'','offset':cursor}, context)
        rows, publisher, actual = result.get('data'), result.get('nickname'), result.get('ghid')
        if actual != ghid: raise DataAdapterError('wechat_account_mismatch')
        if not isinstance(publisher,str) or not publisher.strip() or len(publisher)>100: raise DataAdapterError('upstream_schema')
        if not isinstance(rows,list) or len(rows)>1000 or any(not isinstance(r,dict) for r in rows): raise DataAdapterError('upstream_schema')
        end, next_cursor = result.get('is_end'), result.get('offset')
        if type(end) is not int or end not in {0,1}: raise DataAdapterError('upstream_schema')
        if not end and (not isinstance(next_cursor,str) or not next_cursor or len(next_cursor)>512 or any(ord(c)<32 for c in next_cursor)):
            raise DataAdapterError('upstream_schema')
        # Keep only fields used by the adapter; never persist vendor balances,
        # credentials, tracking URLs, or unneeded response payloads.
        clean = []
        for row in rows:
            item = {k: row[k] for k in ('title','digest','post_time','post_time_str','original') if k in row}
            try: item['url'] = normalize_wechat_url(row.get('url'))
            except DataAdapterError: item['url'] = ''
            clean.append(item)
        return WechatHistoryPage(tuple(clean),publisher,ghid,next_cursor if not end else None,not bool(end),fetched)

    def article(self, url, *, context, max_chars):
        """Read vendor-supplied HTML, retaining the original article reference."""
        url = normalize_wechat_url(url)
        result, fetched = self._call('article_html', {'url':url}, context)
        row = result.get('data')
        if not isinstance(row,dict) or not isinstance(row.get('html'),str): raise DataAdapterError('upstream_schema')
        actual = normalize_wechat_url(row.get('article_url'))
        # Long URLs must match; short URLs can resolve to a long article URL.
        if urlsplit(url).path == '/s' and actual != url: raise DataAdapterError('wechat_article_mismatch')
        return _document(row['html'], actual, fetched, max_chars=max_chars, vendor=True, metadata=row)


# Kept as a private compatibility alias for existing parser checks.
from ir_search.documents.wechat_html import WechatHTMLParser as _ArticleParser, _clip_article, _validate_article


def _document(html, url, fetched, *, max_chars, vendor=False, metadata=None):
    if type(max_chars) is not int or not 1 <= max_chars <= 100000: raise ValueError('Invalid text limit')
    parser=_ArticleParser(url)
    try: parser.feed(html); parser.close()
    except (ValueError, RecursionError): raise DataAdapterError('upstream_schema') from None
    metadata=metadata or {}

    try: text, article = parser.content(vendor=vendor, max_chars=100000)
    except ValueError: raise DataAdapterError('upstream_schema') from None
    if parser.challenge_visible(): raise DataAdapterError('wechat_origin_unavailable')
    title=metadata.get('title') or parser.meta.get('og:title') or ''.join(parser.title).strip()
    if not isinstance(title,str) or not title.strip() or len(title)>1000: raise DataAdapterError('no_extracted_text')
    if not text or text.strip().lower().strip('.… ') in {'加载中', '正在加载', 'loading', 'please wait'} or (len(text)<300 and any(t in text for t in ('环境异常','访问过于频繁','请完成验证','该内容已被发布者删除','此内容因违规无法查看','该内容暂时无法访问'))):
        raise DataAdapterError('wechat_origin_unavailable')
    publication=_published(metadata)
    if not publication and not vendor:
        match=re.search(r'\b(?:var\s+)?(?:ct|publish_time)\s*[=:]\s*[\"\x27]?(\d{10})',html)
        if match: publication=_published({'post_time':match.group(1)})
    publisher=metadata.get('nickname') or ''.join(parser.publisher).strip() or 'unknown'
    if not isinstance(publisher,str) or len(publisher)>200: publisher='unknown'
    warnings=['publisher_identity_not_independently_verified','images_and_media_not_transcribed']
    if len(text)<200: warnings.append('text_extraction_short')
    warnings.append('vendor_text_not_original_file' if vendor else 'origin_text_extracted_not_independently_verified')
    if not publication: warnings.append('published_date_unknown')
    if len(text)>max_chars: warnings.append('text_truncated')
    text=text[:max_chars]; digest=hash_text(text)
    article = _clip_article(article, text)
    if parser.initial_visibility: warnings.append('wechat_initial_visibility_recovered')
    if article.get('structure_truncated'): warnings.append('article_structure_truncated')
    if article.get('links_truncated'): warnings.append('wechat_links_truncated')
    author=metadata.get('author') or ''.join(parser.author).strip()
    article['metadata'] = {'title':title, 'publisher':publisher, 'authors':[author] if isinstance(author,str) and author.strip() and len(author)<=200 else [],
        'published_at':publication.isoformat() if publication else None, 'original_url':url,
        'source_text_trust':'untrusted', 'publisher_identity_verified':False}
    return Document(make_doc_id(url,digest),url,url,title,'wechat',SourceTier.MEDIA,EvidenceType.UNKNOWN,'wechat',
        publication,fetched,'dajiala_article_html' if vendor else 'wechat_article_html',text,text_hash=digest,warnings=warnings,
        extra={'adapter_mode':'live','publisher':publisher,'text_provider':'dajiala' if vendor else 'wechat_origin',
               'article':article, 'public_links':article.get('links', []), 'parser_version':3,
               'authors':[author] if isinstance(author,str) and author.strip() and len(author)<=200 else [],'source_text_trust':'untrusted'})


def _browser_eligible(html):
    """Only observed loading/hidden article gaps; do not automate challenge pages."""
    if _challenge(html): return False
    lower = html.lower()
    return '<script' in lower and ('js_content' in lower or '加载中' in html or 'loading' in lower)


def _limited(document, max_chars, details):
    text = document.text[:max_chars]
    warnings = list(document.warnings)
    if len(document.text) > max_chars and 'text_truncated' not in warnings: warnings.append('text_truncated')
    digest = hash_text(text)
    return replace(document, text=text, text_hash=digest, doc_id=make_doc_id(document.url, digest),
        warnings=warnings, extra={**document.extra, 'web_read': details, 'article':_clip_article(document.extra.get('article', {}), text)})


def fetch_wechat_document(url, *, context, max_chars=20000, client=None, transport=None,
                          mode='auto', cache_mode='use', cache=None, renderer=None):
    """Read a cached snapshot, public HTTP, optional Crawl4AI, then configured vendor.

    cache_mode=refresh skips snapshots but replaces successful entries; off neither
    reads nor writes. mode=http disables the browser, browser explicitly tries it
    after an unsuccessful ordinary read. Failed refreshes never return stale text.
    """
    if mode in ('scrapling', 'firecrawl'):
        raise DataAdapterError('web_content_unsupported')  # Dedicated WeChat extraction remains separate.
    if mode not in ('http', 'auto', 'browser') or cache_mode not in ('use', 'refresh', 'off'):
        raise ValueError('Invalid WeChat read options')
    if type(max_chars) is not int or not 1 <= max_chars <= 100000: raise ValueError('Invalid text limit')
    context.check_active()
    url = normalize_wechat_url(url)
    try: store = cache or (client._cache if isinstance(client, DajialaMaterialClient) else _default_cache())
    except SourceConfigError: raise DataAdapterError('source_config_error') from None
    with store._guard(context) if cache_mode != 'off' else nullcontext(False) as enabled:
        cached = store._get(_ARTICLE_CACHE, url, 86400) if enabled and cache_mode == 'use' else None
        if isinstance(cached, dict):
            try:
                document = document_from_dict(cached)
                _validate_article(document.extra.get('article'), document.text)
                actual = normalize_wechat_url(document.url)
                if ((urlsplit(url).path == '/s' and actual != url) or not document.text.strip() or document.errors
                        or document.source != 'wechat' or document.text_hash != hash_text(document.text)
                        or len(document.text) > 100000 or document.fetched_at.utcoffset() is None
                        or document.source_tier != SourceTier.MEDIA or document.evidence_type != EvidenceType.UNKNOWN
                        or document.extra.get('text_provider') not in ('wechat_origin', 'wechat_browser', 'dajiala')
                        or document.extra.get('generated') or document.extra.get('adapter_mode') != 'live'):
                    raise ValueError()
            except (ValueError, TypeError, KeyError, DataAdapterError, AttributeError):
                store.warning = 'wechat_cache_invalid'
            else:
                document.warnings.append('wechat_article_cache_hit')
                details = {'content_state': 'article_text', 'cache_state': 'hit', 'cache_ttl_seconds': 86400,
                    'snapshot_fetched_at': document.fetched_at.isoformat(), 'freshness': 'cached_snapshot_not_revalidated',
                    'attempts': [{'reader': 'cache', 'state': 'hit'}], 'vendor_body_calls': 0,
                    'browser_attempted': False, 'text_provider': document.extra['text_provider']}
                context.check_active()
                return _limited(document, max_chars, details)
        document, details = _fetch_uncached(url, context=context, client=client, transport=transport,
            mode=mode, renderer=renderer)
        context.check_active()
        details.update(cache_state='refresh' if cache_mode == 'refresh' else 'miss' if enabled else 'off',
                       cache_ttl_seconds=86400, snapshot_fetched_at=document.fetched_at.isoformat(), freshness='fetched_now')
        if enabled:
            # Store the largest supported extraction, independent of the current
            # skill's output limit. A later larger request does not pay again.
            clean = replace(document, extra={k: v for k, v in document.extra.items() if k != 'web_read'})
            store._put(_ARTICLE_CACHE, url, clean.to_dict())
            if document.url != url: store._put(_ARTICLE_CACHE, document.url, clean.to_dict())
        if store.warning and cache_mode != 'off': document.warnings.append(store.warning)
        return _limited(document, max_chars, details)


def _fetch_uncached(url, *, context, client, transport, mode, renderer):
    transport = transport or _request
    attempts = []
    details = {'content_state': 'article_text', 'attempts': attempts, 'vendor_body_calls': 0, 'browser_attempted': False}
    html = ''
    try:
        reply = transport(url, context=_PhaseContext(context, 6), max_bytes=8*1024*1024)
        if reply.status != 200 or 'html' not in reply.content_type: raise DataAdapterError('wechat_origin_unavailable')
        html = reply.body.decode('utf-8', errors='replace')
        document = _document(html, url, reply.fetched_at, max_chars=100000)
        attempts.append({'reader': 'http', 'state': 'ok'})
        details['text_provider'] = 'wechat_origin'
        return document, details
    except DataAdapterError as exc:
        original_failure = exc.code
    except RequestStopped as exc:
        context.check_active()
        if exc.code != 'deadline_exceeded': raise
        original_failure = 'timeout'
    attempts.append({'reader': 'http', 'state': original_failure})
    # Browser never retries transport/TLS/policy failures or attempts challenges.
    eligible = bool(html) and _browser_eligible(html)
    if mode != 'http' and (eligible or mode == 'browser' and bool(html) and not _challenge(html)):
        if context.remaining_seconds() > 12 and context.max_operations - context.operations >= 4:
            details['browser_attempted'] = True
            try:
                rendered = (renderer or render_public_html)(url, context=_BrowserPhase(context), allowed_domains=('mp.weixin.qq.com',))
                actual = normalize_wechat_url(rendered.url)
                if urlsplit(url).path == '/s' and actual != url: raise DataAdapterError('wechat_article_mismatch')
                document = _document(rendered.html, actual, rendered.fetched_at, max_chars=100000)
                document.extraction_method = 'crawl4ai_render_wechat_article_html'
                document.extra['text_provider'] = 'wechat_browser'
                document.warnings.append('wechat_browser_fallback_used')
                attempts.append({'reader': 'crawl4ai', 'state': 'ok'})
                details.update(text_provider='wechat_browser', browser_diagnostics=rendered.diagnostics)
                return document, details
            except (DataAdapterError, RequestStopped) as exc:
                context.check_active()
                if isinstance(exc, RequestStopped) and exc.code == 'cancelled': raise
                attempts.append({'reader': 'crawl4ai', 'state': exc.code})
        else:
            attempts.append({'reader': 'crawl4ai', 'state': 'skipped_insufficient_budget'})
            # A budget cut must not silently choose a paid body read over the free browser.
            raise DataAdapterError('wechat_browser_budget_insufficient')
    elif mode != 'http':
        attempts.append({'reader': 'crawl4ai', 'state': 'skipped_no_loading_gap'})
    if client is None:
        try: profile = wechat_profile()
        except SourceConfigError: raise DataAdapterError('source_config_error') from None
        if profile: client = DajialaMaterialClient(profile)
    if client is None: raise DataAdapterError(original_failure)
    document = client.article(url, context=context, max_chars=100000)
    document.warnings.extend(['wechat_origin_fetch_failed', 'wechat_vendor_fallback_used', 'origin_failure_' + original_failure])
    for attempt in attempts:
        if attempt['reader'] == 'crawl4ai': document.warnings.append('wechat_browser_' + attempt['state'])
    attempts.append({'reader': 'dajiala', 'state': 'ok'})
    details.update(text_provider='dajiala', vendor_body_calls=1)
    return document, details


def _challenge(html):
    lower = html.lower()
    return any(word in html for word in ('环境异常', '请完成验证', '访问过于频繁', '该内容已被发布者删除', '此内容因违规无法查看')) or any(
        word in lower for word in ('captcha', 'verifycode', 'wappoc_appmsgcaptcha'))
