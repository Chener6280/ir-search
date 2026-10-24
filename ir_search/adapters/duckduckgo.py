"""Keyless HTML discovery. Bot challenges are failures, never successful empty pages."""
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlsplit, parse_qs
from ir_search.infrastructure.web_engine import _SearchAdapter
from ir_search.registry import DataAdapterError


class _Results(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.current, self.capture, self.depth = [], None, None, 0
        self.empty, self.challenge = False, False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get('class', '').split()
        if 'no-results__message' in classes: self.empty = True
        if attrs.get('id') == 'challenge-form' or 'anomaly-modal__box' in classes: self.challenge = True
        if tag == 'a' and 'result__a' in classes:
            href = urljoin('https://html.duckduckgo.com', attrs.get('href', ''))
            url = parse_qs(urlsplit(href).query).get('uddg', [href])[0]
            self.current = {'url': url, 'title': '', 'snippet': ''}
            self.rows.append(self.current)
            self.capture, self.depth = 'title', 1
        elif 'result__snippet' in classes and self.current:
            self.capture, self.depth = 'snippet', 1
        elif self.capture: self.depth += 1

    def handle_endtag(self, tag):
        if self.capture:
            self.depth -= 1
            if self.depth <= 0: self.capture = None

    def handle_data(self, data):
        if self.capture: self.current[self.capture] += data


class DuckDuckGoAdapter(_SearchAdapter):
    name = 'duckduckgo'
    endpoint = 'https://html.duckduckgo.com/html/'

    def _search(self, query, limit, context, key):
        reply = self._request(self.endpoint + '?' + urlencode({'q': query}), context,
                              method='GET', headers={'Accept': 'text/html'})
        if reply.status == 429: raise DataAdapterError('rate_limit')
        if reply.status not in {200, 202}:
            from ir_search.infrastructure.web_search import _search_value
            _search_value(reply)
        if 'text/html' not in reply.content_type: raise DataAdapterError('upstream_schema')
        parser = _Results()
        parser.feed(reply.body.decode('utf-8'))
        if parser.challenge:
            context._mark_public_host_limited('html.duckduckgo.com')
            raise DataAdapterError('web_content_challenge')
        if reply.status == 202: raise DataAdapterError('rate_limit')
        if not parser.rows and not parser.empty: raise DataAdapterError('upstream_schema')
        from ir_search.infrastructure.web_engine import _rows
        return _rows(parser.rows[:limit], (('title', 'title'), ('url', 'url'), ('snippet', 'snippet'))), reply.fetched_at
