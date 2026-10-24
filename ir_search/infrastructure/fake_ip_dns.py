"""Opt-in exact-host real DNS for public reads behind a fake-IP resolver.

Only encrypted resolution uses the explicitly configured loopback HTTP proxy.
The page connection still pins a validated public address and obeys OS routing.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
from urllib.parse import urlencode, urlsplit

from ir_search.registry import DataAdapterError


def _settings(host, values=None):
    from .credentials import read_credentials, SourceConfigError
    try:
        values = read_credentials() if values is None else values
        names = values.get('PUBLIC_WEB_REAL_DNS_HOSTS', '')
        if not names:
            return None
        hosts = names.split(',')
        if len(hosts) > 64 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?', h)
                or '.' not in h or '*' in h for h in hosts):
            raise ValueError()
        if host not in hosts:
            return None
        proxy = urlsplit(values.get('PUBLIC_WEB_REAL_DNS_PROXY', ''))
        if (proxy.scheme != 'http' or proxy.hostname != '127.0.0.1'
                or proxy.username is not None or proxy.password is not None
                or proxy.path not in ('', '/') or proxy.query or proxy.fragment
                or proxy.port is None or not 1 <= proxy.port <= 65535):
            raise ValueError()
        return proxy.port
    except (ValueError, TypeError, SourceConfigError):
        raise DataAdapterError('source_config_error') from None


def _unique(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError()
        obj[key] = value
    return obj


def _parse(raw, host, port):
    """No cache, no stale reuse, no private/fake answer exemption."""
    try:
        doc = json.loads(raw, object_pairs_hook=_unique)
        if (type(doc.get('Status')) is not int or doc['Status'] != 0
                or doc.get('TC') is not False
                or doc.get('Question') != [{'name':host, 'type':1}]
                and doc.get('Question') != [{'name':host+'.', 'type':1}]):
            raise ValueError()
        rows = doc['Answer']
        if not isinstance(rows, list) or not 1 <= len(rows) <= 64:
            raise ValueError()
        names = {host.rstrip('.')+'.'}
        ttl = 300
        for row in rows:
            if (not isinstance(row, dict) or type(row.get('TTL')) is not int
                    or row['TTL'] < 0 or not isinstance(row.get('name'), str)):
                raise ValueError()
            ttl = min(ttl, row['TTL'])
        for _ in range(8):
            for row in rows:
                if row.get('type') == 5 and row['name'].rstrip('.')+'.' in names:
                    target = row.get('data')
                    if not isinstance(target, str) or not re.fullmatch(r'[A-Za-z0-9.-]+',target):
                        raise ValueError()
                    names.add(target.rstrip('.')+'.')
        addresses = []
        for row in rows:
            if row.get('type') != 1:
                continue
            ip = ipaddress.IPv4Address(row['data'])
            if row['name'].rstrip('.')+'.' not in names or not ip.is_global:
                raise DataAdapterError('blocked_url')
            addresses.append(str(ip))
        if not addresses:
            raise ValueError()
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', (a, port))
                for a in addresses], ttl
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise DataAdapterError('upstream_schema') from None


def _query(host, proxy_port, context):
    context.check_active()
    context.begin_operation()
    tls = ssl.create_default_context()
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    # HTTPSConnection tunnels through the fixed loopback proxy, then verifies
    # the resolver's original TLS hostname. No inherited proxy/auth/cookies.
    conn = http.client.HTTPSConnection('127.0.0.1', proxy_port,
        timeout=min(6, context.remaining_seconds()), context=tls)
    conn.set_tunnel('cloudflare-dns.com', 443)
    try:
        target = '/dns-query?' + urlencode({'name':host, 'type':'A'})
        conn.request('GET', target, headers={'Host':'cloudflare-dns.com',
            'Accept':'application/dns-json', 'Connection':'close'})
        active_socket = conn.sock
        reply = conn.getresponse()
        if reply.status != 200:
            raise DataAdapterError('network')
        content = reply.getheader('Content-Type', '').split(';',1)[0].strip().lower()
        if content not in ('application/dns-json','application/json'):
            raise DataAdapterError('upstream_schema')
        length = reply.getheader('Content-Length')
        if length is not None and (not length.isdigit() or int(length) > 65536):
            raise DataAdapterError('response_too_large')
        raw = bytearray()
        while True:
            context.check_active()
            active_socket.settimeout(min(6, context.remaining_seconds()))
            chunk = reply.read1(min(8192,65537-len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw)>65536:
                break
        context.check_active()
        if len(raw) > 65536:
            raise DataAdapterError('response_too_large')
        return bytes(raw)
    except (OSError, http.client.HTTPException):
        raise DataAdapterError('network') from None
    finally:
        conn.close()


def _real_rows(host, port, context, values=None):
    proxy_port = _settings(host, values)
    if proxy_port is None:
        return None
    began = time.monotonic()
    rows, ttl = _parse(_query(host, proxy_port, context), host, port)
    if ttl <= 0 or time.monotonic()-began >= ttl:
        raise DataAdapterError('upstream_schema')
    context.check_active()
    return rows
