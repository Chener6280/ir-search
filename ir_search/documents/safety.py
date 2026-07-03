from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Iterable, Optional
from urllib.parse import urlparse


class UrlBlockedError(ValueError):
    """Raised when a URL violates the fetch safety policy."""


@dataclass(frozen=True)
class UrlPolicyResult:
    allowed: bool
    reason: str
    normalized_url: str
    host: str = ""


def is_url_allowed(
    url: str,
    *,
    allow_private_network: bool = False,
    allowlist: Optional[Iterable[str]] = None,
) -> UrlPolicyResult:
    """Validate fetch URLs before network access."""

    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").strip().lower()
    normalized = parsed.geturl()
    if not scheme:
        return UrlPolicyResult(False, "missing URL scheme", normalized, host)
    if scheme not in {"http", "https"}:
        return UrlPolicyResult(False, f"blocked URL scheme: {scheme}", normalized, host)
    if not host:
        return UrlPolicyResult(False, "missing URL host", normalized, host)
    if _is_allowlisted(url, host, allowlist):
        return UrlPolicyResult(True, "allowlisted", normalized, host)
    if allow_private_network:
        return UrlPolicyResult(True, "allowed with private-network override", normalized, host)
    if host == "localhost" or host.endswith(".localhost"):
        return UrlPolicyResult(False, "blocked localhost host", normalized, host)
    ip = _parse_ip(host)
    if ip and _is_blocked_ip(ip):
        return UrlPolicyResult(False, f"blocked private or local IP: {ip}", normalized, host)
    return UrlPolicyResult(True, "allowed", normalized, host)


def ensure_url_allowed(
    url: str,
    *,
    allow_private_network: bool = False,
    allowlist: Optional[Iterable[str]] = None,
) -> UrlPolicyResult:
    """Return the policy result or raise UrlBlockedError."""

    result = is_url_allowed(url, allow_private_network=allow_private_network, allowlist=allowlist)
    if not result.allowed:
        raise UrlBlockedError(result.reason)
    return result


def _parse_ip(host: str) -> Optional[ipaddress._BaseAddress]:
    candidate = host.strip("[]")
    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        return None


def _is_blocked_ip(ip: ipaddress._BaseAddress) -> bool:
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_unspecified
        or ip.is_reserved
        or ip.is_multicast
    )


def _is_allowlisted(url: str, host: str, allowlist: Optional[Iterable[str]]) -> bool:
    if not allowlist:
        return False
    for entry in allowlist:
        item = entry.strip().lower()
        if not item:
            continue
        if item == host or host.endswith(f".{item}") or url.lower().startswith(item):
            return True
    return False
