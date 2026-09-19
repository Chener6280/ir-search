"""Browser I/O policy: fulfill requests through the DNS-pinned stdlib transport.

Never use route.continue_() or browser fetch: a deny-all proxy is defense in depth
for unhandled browser traffic. Only public read endpoints can receive POST bodies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from ir_search.infrastructure.public_web import _url, _allowed_domain, _request, _PUBLIC_BROWSER_READS
from ir_search.registry import DataAdapterError


class _LockedContext:
    def __init__(self, context, lock):
        self._context, self._lock = context, lock

    def __getattr__(self, name):
        return getattr(self._context, name)

    def begin_operation(self):
        with self._lock:
            self._context.begin_operation()


@dataclass
class BrowserNetwork:
    url: str
    context: object
    allowed_domains: tuple = ()
    max_bytes: int = 32 * 1024 * 1024
    received_bytes: int = 0
    request_count: int = 0
    redirects: int = 0
    blocked: int = 0
    warnings: set = field(default_factory=set)
    failures: list = field(default_factory=list)
    final_url: str = ""
    _robots: set = field(default_factory=set)
    _lock: object = field(default_factory=RLock, repr=False)
    _reserved_bytes: int = 0
    _charged_bytes: int = 0

    def _validate(self, url, method, main):
        parsed = urlsplit(url)
        original = urlsplit(self.url)
        public_feed = (not main and parsed.scheme == original.scheme == "https"
                       and parsed.netloc == original.netloc
                       and parsed.path in _PUBLIC_BROWSER_READS.get(parsed.netloc, ()))
        parsed, host, _ = _url(url, signed_download=public_feed)
        if parsed.scheme != "https" or main and not _allowed_domain(host, self.allowed_domains):
            raise DataAdapterError("blocked_url")
        if method != "GET" and not (public_feed and method == "POST"):
            raise DataAdapterError("blocked_url")
        return public_feed

    def _read(self, url, **kwargs):
        self.context.check_active()
        with self._lock:
            budget = min(8 * 1024 * 1024, self.max_bytes - self._charged_bytes - self._reserved_bytes)
            if budget <= 0:
                raise DataAdapterError("browser_budget_exhausted")
            self._reserved_bytes += budget
            self.request_count += 1
        consumed = known = 0
        try:
            reply = _request(url, context=_LockedContext(self.context, self._lock), max_bytes=budget,
                             preserve_headers=True, **kwargs)
            consumed = len(reply.body)
            known = consumed
            return reply
        except DataAdapterError as exc:
            if exc.code in {"response_too_large", "network", "timeout"}:
                consumed = budget  # Partial failed reads have unknown byte counts.
            raise
        finally:
            with self._lock:
                self._reserved_bytes -= budget
                self.received_bytes += known
                self._charged_bytes += consumed

    def _check_robots(self, url):
        parsed = urlsplit(url)
        origin = parsed.scheme + "://" + parsed.netloc
        # Rules apply to every main-document path, including redirects.
        robots_url = origin + "/robots.txt"
        cached = next((entry for entry in self._robots if entry[0] == origin), None)
        if cached is None:
            try:
                reply = self._read(robots_url)
                if reply.status != 200:
                    # Avoid unbounded/unsafe robots redirects; refuse this render.
                    raise DataAdapterError("browser_robots_denied")
                robots = reply.body.decode("utf-8", errors="replace")
                if "<html" in robots[:200].lower():
                    raise DataAdapterError("browser_robots_denied")
            except DataAdapterError as exc:
                if exc.code != "not_found":
                    raise
                robots = ""
                self.warnings.add("robots_not_found")
            self._robots.add((origin, robots))
        else:
            robots = cached[1]
        rules = RobotFileParser()
        rules.parse(robots.splitlines())
        if not rules.can_fetch("ir-search", url):
            raise DataAdapterError("browser_robots_denied")

    def fetch(self, url, *, method="GET", body=None, content_type="", main=False):
        public_feed = self._validate(url, method, main)
        if main:
            self._check_robots(url)
        if body and (not public_feed or len(body) > 65536):
            raise DataAdapterError("blocked_url")
        headers = {"Content-Type": "application/json"} if method == "POST" else None
        reply = self._read(url, method=method, body=body, headers=headers, public_feed=public_feed)
        if reply.status != 200:
            target = urljoin(url, reply.location)
            self._validate(target, "GET", main)
            self.redirects += 1
            if not reply.location or self.redirects > 8:
                raise DataAdapterError("web_redirect_limit")
            return reply.status, {"Location": target}, b""
        if main:
            if reply.content_type.split(";")[0].lower() not in {"text/html", "application/xhtml+xml"}:
                raise DataAdapterError("web_content_unsupported")
            self.final_url = url
        return 200, reply.headers or {"Content-Type": reply.content_type}, reply.body

    def diagnostics(self):
        return {"network": "dns_pinned_transport", "requests": self.request_count,
                "received_bytes": self.received_bytes, "blocked_requests": self.blocked,
                "charged_bytes": self._charged_bytes,
                "warnings": sorted(self.warnings), "tls_verified": True,
                "failures": self.failures[:10],
                "images_media_fonts": "disabled", "ambient_auth": "disabled"}
