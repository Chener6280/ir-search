"""Shared bounded platform reads; no browser login, ambient auth or media downloads."""
from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
import hashlib
import json
import re
from urllib.parse import parse_qs, urlsplit, urljoin
from zoneinfo import ZoneInfo

from ir_search.documents.models import Document, make_doc_id
from ir_search.infrastructure.public_web import _request, _url
from ir_search.models import SourceTier, EvidenceType
from ir_search.registry import DataAdapterError


def _platform_url(url):
    parsed, host, _ = _url(url)
    path = parsed.path
    if host in {'www.xiaoyuzhoufm.com', 'xiaoyuzhoufm.com'} and re.fullmatch(r'/episode/[a-f0-9]{24}/?', path):
        return 'xiaoyuzhou', 'https://www.xiaoyuzhoufm.com' + path.rstrip('/'), path.rstrip('/').split('/')[-1]
    if host in {'xueqiu.com', 'www.xueqiu.com'} and re.fullmatch(r'/[0-9]{1,20}/[0-9]{1,20}/?', path):
        return 'xueqiu', 'https://xueqiu.com' + path.rstrip('/'), path.rstrip('/').split('/')[-1]
    if host == 'guba.eastmoney.com' and re.fullmatch(r'/news,[A-Za-z0-9_]{1,30},[0-9]{1,20}\.html', path):
        return 'eastmoney', 'https://guba.eastmoney.com' + path, path.split(',')[-1][:-5]
    if host in {'www.bilibili.com', 'bilibili.com', 'm.bilibili.com'}:
        match = re.fullmatch(r'/video/(BV[A-Za-z0-9]{10}|av[1-9][0-9]{0,19})/?', path)
        if match:
            parts = parse_qs(parsed.query).get('p', ['1'])
            if len(parts) != 1 or not re.fullmatch(r'[1-9][0-9]{0,2}', parts[0]):
                raise DataAdapterError('blocked_url')
            return 'bilibili', 'https://www.bilibili.com/video/' + match[1] + ('?p=' + parts[0] if parts[0] != '1' else ''), match[1]
    if host in {'www.youtube.com', 'youtube.com', 'm.youtube.com', 'youtu.be'}:
        values = parse_qs(parsed.query).get('v', [])
        identifier = values[0] if path == '/watch' and len(values) == 1 else path.lstrip('/') if host == 'youtu.be' else ''
        if path.startswith(('/shorts/', '/live/')) and host != 'youtu.be':
            identifier = path.split('/')[-1]
        if re.fullmatch(r'[A-Za-z0-9_-]{11}', identifier):
            return 'youtube', 'https://www.youtube.com/watch?v=' + identifier, identifier
    raise DataAdapterError('unsupported')


def _platform_host(url):
    """Known platform hosts must not fall through to generic whole-page extraction."""
    return urlsplit(url).hostname in {'www.xiaoyuzhoufm.com', 'xiaoyuzhoufm.com', 'xueqiu.com', 'www.xueqiu.com', 'guba.eastmoney.com',
        'bilibili.com', 'www.bilibili.com', 'm.bilibili.com', 'youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'}


def _read(url, *, context, hosts, cookie='', signed=False, dns_mode='system'):
    for attempt in range(4):
        parsed, host, _ = _url(url, signed_download=signed)
        if parsed.scheme != 'https' or host not in hosts:
            raise DataAdapterError('blocked_url')
        try:
            reply = _request(url, context=context, max_bytes=4 * 1024 * 1024,
                             headers={'Cookie': cookie} if cookie else None, signed_download=signed,
                             **({'dns_mode': dns_mode} if dns_mode != 'system' else {}))
        except DataAdapterError as exc:
            if getattr(exc, 'http_status', None) == 412 and host in {'api.bilibili.com', 'www.bilibili.com', 'xueqiu.com'}:
                raise DataAdapterError('web_content_challenge') from None
            raise
        if reply.status == 200:
            if cookie and cookie.encode() in reply.body:
                raise DataAdapterError('upstream_schema')
            return reply
        target = urljoin(url, reply.location)
        if urlsplit(target).hostname != host:
            raise DataAdapterError('blocked_url')
        url = target
    raise DataAdapterError('web_redirect_limit')


def _json_read(url, **kwargs):
    reply = _read(url, **kwargs)
    try:
        value = json.loads(reply.body)
        if not isinstance(value, dict): raise ValueError()
        return value, reply
    except (ValueError, UnicodeError):
        raise DataAdapterError('upstream_schema') from None


def _embedded_json(html, marker):
    match = re.search(marker + r'\s*=\s*', html)
    if not match: return {}
    try:
        value, _ = json.JSONDecoder().raw_decode(html[match.end():])
        return value if isinstance(value, dict) else {}
    except (ValueError, RecursionError):
        raise DataAdapterError('upstream_schema') from None


class _Text(HTMLParser):
    def __init__(self, selector=None):
        super().__init__(convert_charrefs=True)
        self.selector, self.parts, self.depth, self.active, self.hidden = selector, [], 0, None, 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {'script', 'style', 'noscript'}: self.hidden += 1
        if self.selector and self.selector in attrs.get('class', '').split() and self.active is None:
            self.active = self.depth
        if tag in {'p', 'div', 'br', 'li', 'h1', 'h2', 'blockquote'} and (not self.selector or self.active is not None):
            self.parts.append('\n')
        if tag not in {'br', 'img', 'meta', 'link', 'hr', 'input', 'source', 'wbr'}: self.depth += 1

    def handle_endtag(self, tag):
        if tag in {'br', 'img', 'meta', 'link', 'hr', 'input', 'source', 'wbr'}: return
        self.depth = max(0, self.depth - 1)
        if tag in {'script', 'style', 'noscript'}: self.hidden = max(0, self.hidden - 1)
        if self.active is not None and self.depth <= self.active:
            self.active = None
        if tag in {'p', 'div', 'li', 'blockquote'}: self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden and (not self.selector or self.active is not None): self.parts.append(data)


def _text(html, selector=None):
    if not isinstance(html, str): raise DataAdapterError('upstream_schema')
    parser = _Text(selector)
    parser.feed(html)
    return '\n'.join(line.strip() for line in ''.join(parser.parts).splitlines() if line.strip())


def _document(url, platform, title, text, fetched_at, *, published=None, publisher='', details=None, warnings=()):
    digest = hashlib.sha256(text.encode()).hexdigest()
    return Document(make_doc_id(url, digest), url, url, (title or url)[:2000], 'video' if platform in {'bilibili', 'youtube'} else platform,
        SourceTier.UGC, EvidenceType.OPINION, 'video' if platform in {'bilibili', 'youtube'} else 'html',
        published, fetched_at, platform + '_platform_text', text, text_hash=digest, warnings=list(warnings),
        extra={'adapter_mode': 'live', 'generated': False, 'publisher': publisher or 'unknown',
            'web_read': {'backend': platform, 'content_state': 'article_text' if text else 'metadata_only',
                'platform': platform, 'text_hash': digest, **(details or {})}})


def _epoch(value, *, milliseconds=False):
    try:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0: return None
        return datetime.fromtimestamp(value / (1000 if milliseconds else 1), ZoneInfo('Asia/Shanghai'))
    except (ValueError, OverflowError, OSError):
        return None


def _cookie(name):
    from .credentials import read_credentials, SourceConfigError
    try:
        value = read_credentials().get(name, '')
    except SourceConfigError:
        raise DataAdapterError('source_config_error') from None
    if not isinstance(value, str) or len(value) > 16000 or any(ord(c) < 32 or ord(c) > 126 for c in value):
        raise DataAdapterError('source_config_error')
    return value
