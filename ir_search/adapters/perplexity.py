"""Perplexity Search API only. Never calls chat/completions or synthesizes answers."""
from ir_search.infrastructure.web_engine import _SearchAdapter, _rows, _search_value


class PerplexityAdapter(_SearchAdapter):
    name = 'perplexity'
    endpoint = 'https://api.perplexity.ai/search'

    def _search(self, query, limit, context, key):
        reply = self._request(self.endpoint, context, payload={'query': query, 'max_results': limit},
                              headers={'Authorization': 'Bearer ' + key})
        return _rows(_search_value(reply).get('results'), (('title', 'title'), ('url', 'url'),
            ('snippet', 'snippet'), ('date', 'published_at'))), reply.fetched_at
