"""Serper Google discovery; discard answer boxes and generated text."""
from ir_search.infrastructure.web_engine import _SearchAdapter, _rows, _search_value


class SerperAdapter(_SearchAdapter):
    name = 'serper'
    endpoint = 'https://google.serper.dev/search'

    def _search(self, query, limit, context, key):
        reply = self._request(self.endpoint, context, payload={'q': query, 'num': limit}, headers={'X-API-KEY': key})
        value = _search_value(reply)
        return _rows(value.get('organic'), (('title', 'title'), ('link', 'url'), ('snippet', 'snippet'),
                                            ('date', 'published_at'))), reply.fetched_at
