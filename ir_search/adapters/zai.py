"""China GLM Coding Plan MCP discovery, based on pi-web-access 0.44.0."""
from ir_search.infrastructure.web_engine import _MCPAdapter, _rows


class ZaiAdapter(_MCPAdapter):
    name = 'zai'
    endpoint = 'https://open.bigmodel.cn/api/mcp/web_search_prime/mcp'
    tool_name = 'web_search_prime'

    def _arguments(self, query, limit):
        return {'search_query': query, 'count': limit}

    def _parse(self, value):
        # This tool has no publication date. Crawl dates are never substituted.
        return _rows(value, (('title', 'title'), ('link', 'url'), ('content', 'snippet')))
