"""Fixed search APIs through the machine's existing TLS/proxy/TUN path.

Unlike arbitrary fetched pages these destinations are constants. No redirects,
proxy mutations, DNS exemptions, retry, account switching or raw error logging.
"""
from datetime import datetime, timezone
import socket
import ssl
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from ir_search.context import RequestStopped
from ir_search.infrastructure.public_web import _Reply
from ir_search.registry import DataAdapterError

_POST = {('api.bocha.cn','/v1/web-search'), ('api.anysearch.com','/v1/search'),
         ('api.exa.ai','/search'), ('api.tavily.com','/search'),
         ('api.perplexity.ai','/search'), ('google.serper.dev','/search')}
_MCP = {('agent-toolkit.app.baizhi.cloud','/mcp'), ('open.bigmodel.cn','/api/mcp/web_search_prime/mcp')}
_GET = {('api.search.brave.com','/res/v1/web/search'), ('html.duckduckgo.com','/html/')}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise DataAdapterError('blocked_url')


def _search_request(url, *, context, method='POST', body=None, headers=None, max_bytes=2*1024*1024,
                    search_api_errors=True, preserve_headers=False):
    parsed = urlsplit(url)
    endpoint = (parsed.hostname, parsed.path)
    if (parsed.scheme != 'https' or parsed.port not in {None,443} or parsed.username or parsed.password
            or parsed.fragment or not search_api_errors
            or not (endpoint in _POST and method=='POST' and not parsed.query
                    or endpoint in _MCP and method in {'POST','DELETE'} and not parsed.query
                    or endpoint in _GET and method=='GET' and body is None)):
        raise DataAdapterError('blocked_url')
    context.check_active()
    if not context._wait_for_public_host(parsed.hostname): raise DataAdapterError('rate_limit')
    context.begin_operation()
    request = urllib.request.Request(url, data=body, headers={'User-Agent':'ir-search/0.2',
        'Accept-Encoding':'identity', **(headers or {})}, method=method)
    response = None
    try:
        # ProxyHandler reads existing OS/env settings; never writes them.
        opener = urllib.request.build_opener(_NoRedirect())
        try:
            response = opener.open(request, timeout=min(15, context.remaining_seconds()))
        except urllib.error.HTTPError as exc:
            response = exc  # Parse bounded errors privately, including AnySearch 402.
        status = response.code
        if 300 <= status < 400: raise DataAdapterError('blocked_url')
        if status == 429: context._mark_public_host_limited(parsed.hostname)
        bound = min(max_bytes, 16384) if status >= 400 else max_bytes
        length = response.headers.get('Content-Length')
        if length and (not length.isdigit() or int(length)>bound):
            raise DataAdapterError('response_too_large' if length.isdigit() else 'upstream_schema')
        if response.headers.get('Content-Encoding','identity').lower() != 'identity':
            raise DataAdapterError('upstream_schema')
        raw = bytearray()
        while True:
            context.check_active()
            chunk = response.read1(min(65536, bound+1-len(raw)))
            if not chunk: break
            raw.extend(chunk)
            if len(raw)>bound: raise DataAdapterError('response_too_large')
            # Streamable HTTP POST carries one JSON-RPC response. A complete SSE
            # event may arrive before the server closes its persistent connection.
            if endpoint in _MCP and response.headers.get_content_type()=='text/event-stream':
                data = bytes(raw).replace(b'\r\n',b'\n')
                if data.endswith(b'\n\n') and (b'"result"' in data or b'"error"' in data):
                    break
        context.check_active()
        safe_headers = {}
        if preserve_headers and endpoint in _MCP:
            for key in ('Mcp-Session-Id','MCP-Protocol-Version'):
                value=response.headers.get(key)
                if value: safe_headers[key]=value
        return _Reply(status, response.headers.get('Content-Type',''), '', bytes(raw),
                      datetime.now(timezone.utc), safe_headers)
    except (DataAdapterError, RequestStopped): raise
    except (TimeoutError, socket.timeout): raise DataAdapterError('timeout') from None
    except urllib.error.URLError as exc:
        reason=exc.reason
        code='tls_error' if isinstance(reason,ssl.SSLError) else 'timeout' if isinstance(reason,(TimeoutError,socket.timeout)) else 'network'
        raise DataAdapterError(code) from None
    except ssl.SSLError: raise DataAdapterError('tls_error') from None
    except Exception: raise DataAdapterError('network') from None
    finally:
        if response is not None: response.close()
