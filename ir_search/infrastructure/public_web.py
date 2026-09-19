"""Bounded public HTTP reads with pinned public DNS addresses and no ambient auth."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import http.client
import ipaddress
import re
import socket
import ssl
from threading import Event, Thread
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from ir_search.context import RequestStopped
from ir_search.contracts.materials import _reference
from ir_search.documents.html import extract_html_document
from ir_search.documents.pdf import extract_pdf_document
from ir_search.registry import DataAdapterError

_MAX_BYTES = 8 * 1024 * 1024
_APPLE_PUBLIC_FEEDS = frozenset({
    "/feed/Event.svc/GetEventYearList", "/feed/FinancialReport.svc/GetFinancialReportList",
    "/feed/FinancialReport.svc/GetFinancialReportYearList", "/feed/ContentAsset.svc/GetContentAssetYearList",
})
_PUBLIC_BROWSER_READS = {
    "investor.apple.com": _APPLE_PUBLIC_FEEDS,
    "investor.nvidia.com": frozenset({
        "/Services/FinancialReportService.svc/GetFinancialReportList",
        "/Services/FinancialReportService.svc/GetFinancialReportYearList",
        "/Services/EventService.svc/GetEventList",
    }),
}


@dataclass(frozen=True)
class _Reply:
    status: int
    content_type: str
    location: str
    body: bytes = field(repr=False)
    fetched_at: datetime
    headers: dict = field(default_factory=dict, repr=False)


def _url(value, *, signed_download=False):
    try:
        if signed_download:
            parsed = urlsplit(value)
            _reference(urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")), web_only=True)
            if parsed.scheme != "https" or any(ord(c) < 32 for c in value): raise ValueError()
        else:
            _reference(value, web_only=True)
        if len(value) > 8192 or "\\" in value or any(c.isspace() for c in value):
            raise ValueError()
        parsed = urlsplit(value)
        host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if port != (443 if parsed.scheme == "https" else 80):
            raise ValueError()
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", host) or "." not in host:
                raise ValueError()
        else:
            if not address.is_global:
                raise ValueError()
        return parsed, host, port
    except (ValueError, TypeError, UnicodeError, AttributeError):
        raise DataAdapterError("blocked_url") from None


def _allowed_domain(host, domains):
    return not domains or any(host == domain or host.endswith("." + domain) for domain in domains)


def _resolve(host, port, context):
    context.check_active()
    rows = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    context.check_active()
    if not rows or any(not ipaddress.ip_address(row[4][0].split("%", 1)[0]).is_global for row in rows):
        raise DataAdapterError("blocked_url")
    # Connect to the checked numeric address without a second hostname resolution.
    return rows[0]


class _Connection(http.client.HTTPConnection):
    def __init__(self, host, port, *, address, tls, timeout):
        # HTTPConnection defaults to port 80 even when its socket is wrapped in
        # TLS. Match the scheme before building Host; some HTTPS origins reject
        # an otherwise valid request when the default :443 is included.
        self.default_port = 443 if tls is not None else 80
        super().__init__(host, port=port, timeout=timeout)
        self._address, self._tls = address, tls

    def connect(self):
        family, kind, protocol, _, sockaddr = self._address
        self.sock = socket.socket(family, kind, protocol)
        self.sock.settimeout(self.timeout)
        self.sock.connect(sockaddr)
        if self._tls is not None:
            self.sock = self._tls.wrap_socket(self.sock, server_hostname=self.host)


def _request(url, *, context, method="GET", body=None, headers=None, max_bytes=_MAX_BYTES, signed_download=False,
             scoped_download_headers=None, public_feed=False, preserve_headers=False, dns_mode='system',
             search_api_errors=False):
    parsed, host, port = _url(url, signed_download=signed_download or public_feed)
    # Only fixed search APIs may return a bounded error body to their own parser.
    # Generic page readers retain strict non-200 rejection; no error body is evidence.
    if type(search_api_errors) is not bool or (search_api_errors and (
            method != 'POST' or parsed.scheme != 'https' or parsed.query or parsed.fragment
            or (host, parsed.path) not in {
                ('api.bochaai.com', '/v1/web-search'), ('api.anysearch.com', '/v1/search'),
                ('api.exa.ai', '/search'), ('api.tavily.com', '/search'),
                ('api.firecrawl.dev', '/v2/search')})):
        raise DataAdapterError('blocked_url')
    if dns_mode not in {'system', 'google_doh'} or (dns_mode != 'system' and
            (host != 'www.youtube.com' or parsed.scheme != 'https' or port != 443)):
        raise DataAdapterError('blocked_url')
    if public_feed and (parsed.path not in _PUBLIC_BROWSER_READS.get(host, ())
            or method not in {"GET", "POST"} or headers and set(headers) - {"Content-Type", "Accept"}
            or body and len(body) > 65536):
        raise DataAdapterError("blocked_url")
    if signed_download and (method != "GET" or body is not None or headers):
        raise DataAdapterError("blocked_url")
    if scoped_download_headers is not None:
        # Only temporary IMA/COS download auth, never the long-lived OpenAPI credentials.
        # Redirects are returned to the caller; this transport never forwards headers.
        if (not signed_download or not _allowed_domain(host, ('ima.qq.com', 'myqcloud.com'))
                or not isinstance(scoped_download_headers, dict) or len(scoped_download_headers)>10):
            raise DataAdapterError("blocked_url")
        allowed = {'authorization', 'x-cos-security-token', 'referer', 'user-agent',
                   'x-ima-create-url-time', 'x-ima-platform', 'x-ima-resource-category',
                   'x-ima-sign', 'x-ima-trace-id', 'x-ima-uid-sha256'}
        if (len({k.lower() for k in scoped_download_headers if isinstance(k,str)}) != len(scoped_download_headers)
                or any(not isinstance(k,str) or k.lower() not in allowed or not isinstance(v,str)
                       or (not v and k.lower() != 'x-ima-resource-category')
                       or len(v)>8192 or any(ord(c)<32 or ord(c)>126 for c in v)
                       for k,v in scoped_download_headers.items())):
            raise DataAdapterError("blocked_url")
        headers = scoped_download_headers
    if not context._wait_for_public_host(host):
        raise DataAdapterError('rate_limit')
    context.begin_operation()
    connection, watcher, active_socket = None, None, None
    finished = Event()
    try:
        if dns_mode == 'google_doh':
            from .public_dns import _google_public_address
            address = _google_public_address(host, port, context)
        else:
            address = _resolve(host, port, context)
        tls = None
        if parsed.scheme == "https":
            tls = ssl.create_default_context()
            tls.minimum_version = ssl.TLSVersion.TLSv1_2
        connection = _Connection(host, port, address=address, tls=tls, timeout=context.remaining_seconds())

        def stop_socket():
            while not finished.wait(0.05):
                try:
                    context.check_active()
                except RequestStopped:
                    sock = active_socket or connection.sock
                    if sock is not None:
                        try:
                            sock.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                    return

        watcher = Thread(target=stop_socket, daemon=True)
        watcher.start()
        connection.connect()
        active_socket = connection.sock
        context.check_active()
        active_socket.settimeout(context.remaining_seconds())
        target = urlunsplit(("", "", quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~"),
                              quote(parsed.query, safe="%=&/:?@!$'()*+,;~-._"), ""))
        request_headers = {"User-Agent": "ir-search/0.1", "Accept-Encoding": "identity",
                           "Accept": "text/html,application/pdf,text/plain", **(headers or {})}
        connection.request(method, target, body=body, headers=request_headers)
        response = connection.getresponse()
        if response.status in {301, 302, 303, 307, 308}:
            return _Reply(response.status, "", response.getheader("Location", ""), b"", datetime.now(timezone.utc))
        if response.status == 429:
            context._mark_public_host_limited(host)
        error_reply = search_api_errors and response.status in {400, 401, 402, 403, 429, 432, 433}
        if response.status != 200 and not error_reply:
            code = {401: "authentication_failed", 402: "entitlement_denied", 403: "entitlement_denied",
                    404: "not_found", 429: "rate_limit"}.get(response.status, "network")
            error = DataAdapterError(code)
            error.http_status = response.status  # Safe numeric context, never response bodies or credentials.
            raise error
        if error_reply:
            max_bytes = min(max_bytes, 16384)
        length = response.getheader("Content-Length")
        if length is not None and (not length.isdigit() or int(length) > max_bytes):
            raise DataAdapterError("response_too_large" if length.isdigit() else "upstream_schema")
        if response.getheader("Content-Encoding", "identity").lower() != "identity":
            raise DataAdapterError("web_content_unsupported")
        raw = bytearray()
        while True:
            context.check_active()
            active_socket.settimeout(context.remaining_seconds())
            chunk = response.read1(min(65536, max_bytes + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > max_bytes:
                raise DataAdapterError("response_too_large")
        context.check_active()
        safe_headers = {}
        if preserve_headers:
            for key in ("Content-Type", "Content-Security-Policy", "Access-Control-Allow-Origin",
                        "Access-Control-Allow-Methods", "Access-Control-Allow-Headers", "X-Content-Type-Options"):
                value = response.getheader(key)
                if value:
                    safe_headers[key] = value
        return _Reply(response.status, response.getheader("Content-Type", ""), "", bytes(raw), datetime.now(timezone.utc), safe_headers)
    except (DataAdapterError, RequestStopped):
        raise
    except ssl.SSLError:
        context.check_active()
        raise DataAdapterError("tls_error") from None
    except (TimeoutError, socket.timeout):
        context.check_active()
        raise DataAdapterError("timeout") from None
    except Exception:
        context.check_active()
        raise DataAdapterError("network") from None
    finally:
        finished.set()
        if connection is not None:
            connection.close()
        if watcher is not None:
            watcher.join(timeout=0.2)


def fetch_public_document(url: str, *, context, max_chars: int = 20000, allowed_domains=(), allow_empty=False, html_parser='stdlib'):
    """Read one public document; every redirect and socket read shares the budget."""
    if type(max_chars) is not int or not 1 <= max_chars <= 100000:
        raise ValueError("Invalid text limit")
    if type(allow_empty) is not bool:
        raise ValueError("Invalid empty document policy")
    if html_parser not in {'stdlib', 'scrapling'}:
        raise ValueError('Invalid HTML parser')
    if html_parser == 'scrapling':
        from .web_toolkit import _scrapling_selector
        _scrapling_selector()  # Missing optional dependency fails before a network request.
    if not isinstance(allowed_domains, tuple) or any(not isinstance(d, str) or not d for d in allowed_domains):
        raise ValueError("Invalid domain restriction")
    current, visited, hops = url, set(), 0
    while True:
        parsed, host, _ = _url(current)
        if not _allowed_domain(host, allowed_domains):
            raise DataAdapterError("blocked_url")
        if current in visited:
            raise DataAdapterError("web_redirect_limit")
        visited.add(current)
        reply = _request(current, context=context)
        if reply.status != 200:
            if not reply.location or hops >= 3:
                raise DataAdapterError("web_redirect_limit")
            target = urljoin(current, reply.location)
            next_parsed, _, _ = _url(target)
            if parsed.scheme == "https" and next_parsed.scheme != "https":
                raise DataAdapterError("blocked_url")
            current, hops = target, hops + 1
            continue
        break
    content_type = reply.content_type.split(";", 1)[0].strip().lower()
    if content_type == "application/pdf" or reply.body.startswith(b"%PDF-"):
        document = extract_pdf_document(reply.body, current, max_chars=max_chars)
        if document.errors:
            code = "dependency_missing" if any("not installed" in e for e in document.errors) else "upstream_schema"
            raise DataAdapterError(code)
    elif content_type in {"text/html", "application/xhtml+xml", "text/plain"}:
        charset = re.search(r"charset\s*=\s*[\"']?([\w-]+)", reply.content_type, re.I)
        if not charset:
            charset = re.search(r"charset\s*=\s*[\"']?([\w-]+)", reply.body[:4096].decode("ascii", errors="ignore"), re.I)
        encoding = charset.group(1).lower() if charset else "utf-8"
        if encoding not in {"utf-8", "utf8", "gb2312", "gbk", "gb18030", "big5", "iso-8859-1", "windows-1252"}:
            raise DataAdapterError("web_content_unsupported")
        if encoding in {"gbk", "gb2312"}:
            encoding = "gb18030"
        try:
            decoded = reply.body.decode(encoding)
        except UnicodeError:
            raise DataAdapterError("upstream_schema") from None
        if content_type == "text/plain":
            # Escape markup so literal plaintext cannot become HTML structure.
            from html import escape
            decoded = "<pre>" + escape(decoded) + "</pre>"
        if html_parser == 'scrapling' and content_type != 'text/plain':
            from .web_toolkit import extract_scrapling_document
            document = extract_scrapling_document(decoded.encode('utf-8'), current, max_chars=max_chars)
        else:
            document = extract_html_document(decoded.encode("utf-8"), current, max_chars=max_chars)
        if document.errors:
            raise DataAdapterError("upstream_schema")
    else:
        raise DataAdapterError("web_content_unsupported")
    context.check_active()
    if not document.text.strip() and not allow_empty:
        raise DataAdapterError("no_extracted_text")
    document.fetched_at = reply.fetched_at
    document.extra.update(requested_url=url, redirects=hops, source_text_trust="untrusted", adapter_mode="live")
    if urlsplit(current).scheme == "http":
        document.warnings.append("unencrypted_public_document")
    return document
