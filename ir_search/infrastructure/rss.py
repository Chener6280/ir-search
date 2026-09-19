"""Bounded RSS 2.0 / Atom discovery with no external XML entities or ambient auth."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
import json
from urllib.parse import urljoin, urlunsplit
from xml.etree import ElementTree as ET

from .credentials import read_credentials, SourceConfigError
from .public_web import _url, _request
from ir_search.documents.html import extract_html_document
from ir_search.registry import DataAdapterError

_ATOM = '{http://www.w3.org/2005/Atom}'
_XML_BASE = '{http://www.w3.org/XML/1998/namespace}base'
_MAX_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class RSSProfile:
    feed_urls: tuple[str, ...]

    def __post_init__(self):
        if (not isinstance(self.feed_urls, tuple) or not 1 <= len(self.feed_urls) <= 5
                or len(set(self.feed_urls)) != len(self.feed_urls)):
            raise SourceConfigError()
        try:
            for url in self.feed_urls:
                parsed, _, _ = _url(url)
                if parsed.scheme != 'https' or parsed.fragment:
                    raise SourceConfigError()
        except DataAdapterError:
            raise SourceConfigError() from None


def rss_profile(*, values=None, env_file=None):
    """Load up to five explicit public feeds, disabled by default."""
    values = read_credentials(env_file) if values is None else values
    enabled = values.get('RSS_ENABLED', 'false').lower()
    if enabled not in {'true', 'false'}:
        raise SourceConfigError()
    if enabled == 'false':
        return None
    try:
        urls = json.loads(values.get('RSS_FEED_URLS', '[]'))
        if not isinstance(urls, list) or any(not isinstance(v, str) for v in urls):
            raise ValueError()
        return RSSProfile(tuple(urls))
    except (ValueError, TypeError):
        raise SourceConfigError() from None


@dataclass(frozen=True)
class RSSItem:
    url: str
    title: str
    excerpt: str
    published_at: datetime | None
    date_field: str | None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class RSSPage:
    url: str
    items: tuple[RSSItem, ...]
    fetched_at: datetime
    received_count: int
    warnings: tuple[str, ...] = ()


class _SafeTree(ET.TreeBuilder):
    def __init__(self):
        super().__init__()
        self.depth = self.nodes = 0

    def doctype(self, *args):
        raise ValueError('DTD forbidden')

    def start(self, tag, attrs):
        self.depth += 1
        self.nodes += 1
        if self.depth > 64 or self.nodes > 20000:
            raise ValueError('XML limit')
        return super().start(tag, attrs)

    def end(self, tag):
        self.depth -= 1
        return super().end(tag)


def _text(element):
    return ''.join(element.itertext()).strip() if element is not None else ''


def _plain(element, url, limit):
    if element is None:
        return ''
    # Atom XHTML children need serialization; plain text must never become markup.
    if len(element):
        value = ''.join(ET.tostring(child, encoding='unicode') for child in element)
    elif element.tag.startswith(_ATOM) and element.get('type', 'text') == 'text':
        return _text(element)[:limit]
    else:
        value = _text(element)
    return extract_html_document(value.encode(), url, max_chars=limit).text


def parse_feed(raw: bytes, url: str, *, fetched_at: datetime) -> RSSPage:
    """Parse at most 200 entries; dates are publication fields, never Atom updated."""
    _url(url)
    if not isinstance(raw, bytes) or len(raw) > _MAX_BYTES:
        raise DataAdapterError('response_too_large')
    if not isinstance(fetched_at, datetime) or fetched_at.utcoffset() is None:
        raise ValueError('Aware receipt time required')
    try:
        root = ET.fromstring(raw, parser=ET.XMLParser(target=_SafeTree()))
    except (ET.ParseError, ValueError, RecursionError):
        raise DataAdapterError('upstream_schema') from None
    if root.tag == 'rss' and root.get('version') == '2.0' and root.find('channel') is not None:
        channel = root.find('channel')
        rows, atom = channel.findall('item'), False
        base = urljoin(urljoin(url, root.get(_XML_BASE, '')), channel.get(_XML_BASE, ''))
    elif root.tag == _ATOM + 'feed':
        rows, atom = root.findall(_ATOM + 'entry'), True
        base = urljoin(url, root.get(_XML_BASE, ''))
    else:
        raise DataAdapterError('web_content_unsupported')
    items, warnings = [], []
    if len(rows) > 200:
        warnings.append('rss_entry_limit')
    for row in rows[:200]:
        try:
            prefix = _ATOM if atom else ''
            row_base = urljoin(base, row.get(_XML_BASE, ''))
            link = row.find(prefix + 'link')
            if atom:
                links = [v for v in row.findall(prefix + 'link') if v.get('rel', 'alternate') == 'alternate'
                         and v.get('type', 'text/html') in {'text/html', 'application/xhtml+xml'}]
                if not links:
                    raise ValueError()
                link = links[0]
                href = link.get('href', '')
            else:
                href = _text(link)
            if not href:
                raise ValueError()
            link_base = urljoin(row_base, link.get(_XML_BASE, '')) if link is not None else row_base
            parsed, _, _ = _url(urljoin(link_base, href))
            target = urlunsplit(parsed._replace(fragment=''))
            title = _plain(row.find(prefix + 'title'), target, 1000).strip()
            if not title:
                raise ValueError()
            excerpt = _plain(row.find(prefix + ('summary' if atom else 'description')), target, 10000)
            date_field = 'published' if atom else 'pubDate'
            date_text = _text(row.find(prefix + date_field))
            published, entry_warnings = None, []
            if date_text:
                try:
                    published = datetime.fromisoformat(date_text.replace('Z', '+00:00')) if atom else parsedate_to_datetime(date_text)
                    if published is None:
                        raise ValueError()
                    if published.utcoffset() is None:
                        entry_warnings.append('publication_timezone_unknown')
                except (ValueError, TypeError, OverflowError):
                    entry_warnings.append('publication_date_invalid')
            if published is None:
                entry_warnings.append('publication_date_unknown')
            items.append(RSSItem(target, title, excerpt, published, date_field if date_text else None, tuple(entry_warnings)))
        except (ValueError, TypeError, AttributeError, DataAdapterError):
            warnings.append('invalid_rss_entry')
    return RSSPage(url, tuple(items), fetched_at, min(len(rows), 1000), tuple(dict.fromkeys(warnings)))


def fetch_feed(url, *, context) -> RSSPage:
    """Fetch one feed with three redirects maximum, checking every destination."""
    current, seen = url, set()
    for hop in range(4):
        parsed, _, _ = _url(current)
        if parsed.scheme != 'https' or current in seen:
            raise DataAdapterError('blocked_url')
        seen.add(current)
        reply = _request(current, context=context, max_bytes=_MAX_BYTES,
                         headers={'Accept': 'application/rss+xml,application/atom+xml,application/xml,text/xml'})
        if reply.status == 200:
            if reply.content_type.split(';', 1)[0].strip().lower() not in {
                'application/rss+xml', 'application/atom+xml', 'application/xml', 'text/xml', 'text/plain'}:
                raise DataAdapterError('web_content_unsupported')
            result = parse_feed(reply.body, current, fetched_at=reply.fetched_at)
            context.check_active()
            return result
        if reply.status not in {301,302,303,307,308} or not reply.location:
            raise DataAdapterError('upstream_schema')
        current = urljoin(current, reply.location)
    raise DataAdapterError('web_redirect_limit')
