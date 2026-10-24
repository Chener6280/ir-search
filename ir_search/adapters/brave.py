"""Brave Search API discovery; no answer generation."""
from urllib.parse import urlencode
from ir_search.infrastructure.web_engine import _SearchAdapter, _rows, _search_value


class BraveAdapter(_SearchAdapter):
    name = 'brave'
    endpoint = 'https://api.search.brave.com/res/v1/web/search'

    def _search(self, query, limit, context, key):
        reply = self._request(self.endpoint + '?' + urlencode({'q': query, 'count': limit}), context,
                              method='GET', headers={'X-Subscription-Token': key})
        value = _search_value(reply)
        return _rows(value.get('web', {}).get('results'), (('title', 'title'), ('url', 'url'),
            ('description', 'snippet'), ('page_age', 'published_at'))), reply.fetched_at
