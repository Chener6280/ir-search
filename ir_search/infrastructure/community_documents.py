"""Extract the main public post only; comments, rankings and navigation are excluded."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
import re

from .platform_documents import _platform_url, _read, _embedded_json, _text, _document, _epoch, _cookie
from ir_search.registry import DataAdapterError


def read_community_document(url, *, context, max_chars=20000):
    """Read one Xueqiu or Eastmoney Guba post; access challenges are explicit failures."""
    if type(max_chars) is not int or not 1 <= max_chars <= 100000: raise ValueError('Invalid text limit')
    platform, url, identifier = _platform_url(url)
    if platform not in {'xueqiu', 'eastmoney'}: raise DataAdapterError('unsupported')
    if platform == 'xueqiu':
        from .xueqiu_browser import xueqiu_read_profile, read_xueqiu_browser_document, SourceConfigError
        try: profile = xueqiu_read_profile()
        except SourceConfigError: raise DataAdapterError('source_config_error') from None
        if profile.mode == 'browser':
            return read_xueqiu_browser_document(url, context=context, max_chars=max_chars)
    reply = _read(url, context=context, hosts={'xueqiu.com'} if platform == 'xueqiu' else {'guba.eastmoney.com'},
                  cookie=_cookie('XUEQIU_COOKIE') if platform == 'xueqiu' else '')
    try: html = reply.body.decode('utf-8')
    except UnicodeError: raise DataAdapterError('upstream_schema') from None
    published, warnings = None, ['community_content_not_verified', 'reposts_not_independent_corroboration']
    if platform == 'eastmoney':
        data = _embedded_json(html, r'\bpost_article')
        if not data and re.search(r'_waf_|g-recaptcha|验证后继续访问|人机验证|访问验证|请输入验证码', html, re.I):
            raise DataAdapterError('web_content_challenge')
        if str(data.get('post_id', '')) != identifier: raise DataAdapterError('upstream_schema')
        title, text = _text(data.get('post_title', '')), _text(data.get('post_content', ''))
        user = data.get('post_user') or {}
        if not isinstance(user, dict): raise DataAdapterError('upstream_schema')
        publisher = str(user.get('user_nickname') or '')[:500]
        instant = data.get('post_publish_time')
        if isinstance(instant, str) and re.fullmatch(r'\d{4}-\d\d-\d\d \d\d:\d\d:\d\d', instant):
            try: published = datetime.fromisoformat(instant).replace(tzinfo=ZoneInfo('Asia/Shanghai'))
            except ValueError: warnings.append('publication_date_invalid')
    else:
        data = _embedded_json(html, r'\bSNB\.data\.status')
        if data and str(data.get('id', '')) != identifier: raise DataAdapterError('upstream_schema')
        title = _text(data.get('title', '')) or _text(html, 'article__bd__title')
        text = _text(data.get('text', '')) or _text(html, 'article__bd__detail')
        user = data.get('user') or {}
        if not isinstance(user, dict): raise DataAdapterError('upstream_schema')
        publisher = str(user.get('screen_name') or '')[:500]
        published = _epoch(data.get('created_at'), milliseconds=True)
    # Login widgets and posts discussing verification can contain these words.
    # Only a page without an identified main post is classified as a challenge;
    # the wrapper/navigation itself is never accepted as source text.
    if not text:
        if re.search(r'_waf_|g-recaptcha|验证后继续访问|人机验证|访问验证|请输入验证码', html, re.I):
            raise DataAdapterError('web_content_challenge')
        raise DataAdapterError('no_extracted_text')
    if not published: warnings.append('published_date_unknown')
    if len(text) > max_chars: warnings.append('text_truncated')
    context.check_active()
    return _document(url, platform, title, text[:max_chars], reply.fetched_at, published=published,
        publisher=publisher, warnings=warnings, details={'content_origin': 'community_post',
            'comments_included': False, 'source_attribution_preserved': True})
