"""Shared bounded search transport. Remote errors and MCP content are never logged."""
from __future__ import annotations

from datetime import datetime
import json
from time import monotonic

from ir_search.context import RequestContext, RequestStopped
from ir_search.infrastructure.search_transport import _search_request as _request
from ir_search.infrastructure.web_search import WebSearchPage, _failure_code, _search_value, _constant, _MAX_BYTES
from ir_search.registry import DataAdapterError


class _EngineContext:
    """Give discovery a deadline while retaining the caller's shared operation budget."""
    def __init__(self, parent):
        self.parent, self.deadline = parent, monotonic() + 15

    def check_active(self):
        self.parent.check_active()
        if monotonic() >= self.deadline: raise DataAdapterError('timeout')

    def remaining_seconds(self):
        self.check_active()
        return min(self.parent.remaining_seconds(), max(0.001, self.deadline - monotonic()))

    def begin_operation(self):
        self.check_active()
        self.parent.begin_operation()

    def _wait_for_public_host(self, host):
        self.check_active()
        return self.parent._wait_for_public_host(host)

    def _mark_public_host_limited(self, host):
        self.parent._mark_public_host_limited(host)


def _rows(value, mapping):
    if not isinstance(value, list) or len(value) > 100 or any(not isinstance(row, dict) for row in value):
        raise DataAdapterError('upstream_schema')
    rows = []
    for row in value:
        result = {dest: row[src] for src, dest in mapping if src in row}
        if (not isinstance(result.get('url'), str) or not result['url'].startswith(('https://', 'http://'))
                or not isinstance(result.get('title'), str) or not result['title'].strip()
                or any(not isinstance(v, str) and v is not None for v in result.values())):
            raise DataAdapterError('upstream_schema')
        rows.append(result)
    return rows


class _SearchAdapter:
    mode = 'live'

    def __init__(self, profile, *, transport=None):
        from .credentials import WebMaterialProfile
        if not isinstance(profile, WebMaterialProfile): raise ValueError('WebMaterialProfile required')
        self._profile, self._transport = profile, transport or _request

    def _key(self):
        key = self._profile.credential(self.name)
        if self.name != 'duckduckgo' and not key: raise DataAdapterError('no_credentials')
        return key

    def _request(self, url, context, *, payload=None, headers=None, method='POST', preserve_headers=False):
        try:
            return self._transport(url, method=method, body=json.dumps(payload).encode() if payload is not None else None,
                headers={'Content-Type': 'application/json', 'Accept': 'application/json', **(headers or {})},
                max_bytes=_MAX_BYTES, context=context, search_api_errors=True, preserve_headers=preserve_headers)
        except (DataAdapterError, RequestStopped): raise
        except TimeoutError: raise DataAdapterError('timeout') from None
        except Exception: raise DataAdapterError('network') from None

    def search(self, query, *, limit, context):
        if not isinstance(query, str) or not query.strip() or len(query) > 4000 or type(limit) is not int or not 1 <= limit <= 10:
            raise DataAdapterError('unsupported')
        context.check_active()
        key = self._key()
        try:
            rows, fetched_at = self._search(query, limit, context, key)
            if key and key in json.dumps(rows, ensure_ascii=False): raise DataAdapterError('upstream_schema')
            context.check_active()
            return WebSearchPage(rows[:limit], fetched_at)
        except (DataAdapterError, RequestStopped): raise
        except TimeoutError: raise DataAdapterError('timeout') from None
        except Exception: raise DataAdapterError('upstream_schema') from None

    def query(self, q):
        """Compatibility shape for direct adapter callers; not wired into legacy routes."""
        from ir_search.models import Hit
        page = self.search(q.text, limit=q.count, context=RequestContext())
        hits = []
        for row in page.rows:
            published = None
            try: published = datetime.fromisoformat(row.get('published_at', '').replace('Z', '+00:00'))
            except (ValueError, AttributeError): pass
            hits.append(Hit(title=row['title'], url=row['url'], snippet=row.get('snippet') or '',
                            published_at=published, source=self.name))
        return hits


class _MCPAdapter(_SearchAdapter):
    """Streamable HTTP lifecycle and Bearer auth from local pi-web-access 0.44.0."""
    def _search(self, query, limit, context, key):
        session = None
        protocol = '2024-11-05'
        base_headers = {'Authorization': 'Bearer ' + key, 'Accept': 'application/json, text/event-stream'}

        def rpc(method, params=None, request_id=None):
            nonlocal session, protocol
            headers = {**base_headers}
            if session: headers['Mcp-Session-Id'] = session
            if method != 'initialize': headers['MCP-Protocol-Version'] = protocol
            body = {'jsonrpc': '2.0', 'method': method}
            if params is not None: body['params'] = params
            if request_id is not None: body['id'] = request_id
            reply = self._request(self.endpoint, context, payload=body, headers=headers, preserve_headers=True)
            if reply.status in {202, 204} and request_id is None: return {}, reply.fetched_at
            if reply.content_type.split(';')[0].strip() == 'text/event-stream' and reply.status == 200:
                # A bounded POST response, no background SSE connection or reconnection.
                messages = []
                for event in reply.body.decode('utf-8').replace('\r\n', '\n').split('\n\n'):
                    data = '\n'.join(l[5:].lstrip() for l in event.splitlines() if l.startswith('data:'))
                    if data: messages.append(json.loads(data, parse_constant=_constant))
                matches = [v for v in messages if isinstance(v, dict) and v.get('id') == request_id]
                if len(matches) != 1: raise DataAdapterError('upstream_schema')
                value = matches[0]
            else: value = _search_value(reply)
            if value.get('jsonrpc') != '2.0' or value.get('id') != request_id: raise DataAdapterError('upstream_schema')
            if value.get('error'):
                raise DataAdapterError(_failure_code(200, value['error']))
            result = value.get('result')
            if not isinstance(result, dict): raise DataAdapterError('upstream_schema')
            headers = {k.lower(): v for k, v in reply.headers.items()}
            new_session = headers.get('mcp-session-id')
            if new_session:
                if not isinstance(new_session, str) or not 1 <= len(new_session) <= 256 or any(ord(c)<33 or ord(c)>126 for c in new_session):
                    raise DataAdapterError('upstream_schema')
                session = new_session
            return result, reply.fetched_at

        try:
            result, _ = rpc('initialize', {'protocolVersion': protocol, 'capabilities': {},
                'clientInfo': {'name': 'ir-search', 'version': '0.2.0rc2'}}, 1)
            protocol = result.get('protocolVersion')
            if protocol not in {'2024-11-05', '2025-03-26', '2025-06-18'}: raise DataAdapterError('upstream_schema')
            rpc('notifications/initialized')
            result, fetched = rpc('tools/call', {'name': self.tool_name, 'arguments': self._arguments(query, limit)}, 2)
            if result.get('isError'):
                # Inspect declared error text privately, expose only normalized category.
                text = ' '.join(p.get('text', '') for p in result.get('content', []) if isinstance(p, dict) and isinstance(p.get('text'), str))
                raise DataAdapterError(_failure_code(200, {'message': text}))
            value = result.get('structuredContent')
            if value is None:
                parts = [p['text'] for p in result.get('content', []) if isinstance(p, dict) and p.get('type') == 'text' and isinstance(p.get('text'), str)]
                if len(parts) != 1: raise DataAdapterError('upstream_schema')
                value = json.loads(parts[0], parse_constant=_constant)
                if isinstance(value, str): value = json.loads(value, parse_constant=_constant)
            return self._parse(value), fetched
        finally:
            if session:
                try:
                    self._request(self.endpoint, context, headers={**base_headers, 'Mcp-Session-Id': session,
                        'MCP-Protocol-Version': protocol or '2024-11-05'}, method='DELETE')
                except (DataAdapterError, RequestStopped): pass
