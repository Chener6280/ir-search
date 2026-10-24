"""Baizhi MCP discovery, based on local pi-web-access 0.44.0."""
from ir_search.infrastructure.web_engine import _MCPAdapter, _rows


class BaizhiAdapter(_MCPAdapter):
    name = 'baizhi'
    endpoint = 'https://agent-toolkit.app.baizhi.cloud/mcp'
    tool_name = 'websearch_search'

    def _arguments(self, query, limit):
        return {'query': query, 'count': limit, 'need_summary': False, 'time_range': 'month'}

    def _parse(self, value):
        # Explicit containers only; arbitrary prose is never converted into evidence.
        if isinstance(value, dict): value = value.get('results')
        return _rows(value, (('title', 'title'), ('url', 'url'), ('snippet', 'snippet'),
                             ('published_at', 'published_at')))
