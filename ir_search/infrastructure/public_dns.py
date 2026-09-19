"""Opt-in encrypted resolution for the fixed YouTube public reader only."""
from __future__ import annotations

import ipaddress
import json
import socket
import time
from urllib.parse import urlencode

from ir_search.registry import DataAdapterError


def _youtube_dns_mode(values=None):
    from .credentials import read_credentials, SourceConfigError
    try: values = read_credentials() if values is None else values
    except SourceConfigError: raise DataAdapterError('source_config_error') from None
    mode = values.get('YOUTUBE_DNS_MODE', 'system')
    if mode not in {'system', 'google_doh'}: raise DataAdapterError('source_config_error')
    return mode


def _google_public_address(host, port, context):
    """Resolve a fixed hostname; retain public-IP checks and the target TLS name."""
    from .public_web import _request
    if host != 'www.youtube.com' or port != 443: raise DataAdapterError('blocked_url')
    context.check_active()
    cached = getattr(context, '_youtube_dns_cache', None)
    if cached and cached[0] > time.monotonic(): return cached[1]
    reply = _request('https://dns.google/resolve?' + urlencode({
        'name': host, 'type': 'A', 'edns_client_subnet': '0.0.0.0/0'}),
        context=context, max_bytes=65536)
    if reply.status != 200: raise DataAdapterError('blocked_url')
    try:
        value = json.loads(reply.body)
        if (type(value.get('Status')) is not int or value['Status'] != 0 or value.get('TC') is not False
                or value.get('Question') != [{'name':host+'.', 'type':1}]): raise ValueError()
        rows = value['Answer']
        if not isinstance(rows, list) or not 1 <= len(rows) <= 64 or any(not isinstance(r,dict) for r in rows): raise ValueError()
        names = {host+'.'}
        for _ in range(8):
            for row in rows:
                if row.get('type') == 5 and row.get('name') in names and isinstance(row.get('data'),str):
                    names.add(row['data'].rstrip('.')+'.')
        records = [r for r in rows if r.get('type') == 1]
        if not records: raise ValueError()
        ttl = 300
        for row in records:
            ip = ipaddress.IPv4Address(row['data'])
            if row.get('name') not in names or not ip.is_global: raise DataAdapterError('blocked_url')
            if type(row.get('TTL')) is not int or row['TTL'] < 0: raise ValueError()
            ttl = min(ttl, row['TTL'])
        address = (socket.AF_INET, socket.SOCK_STREAM, 0, '', (records[0]['data'], port))
        context._youtube_dns_cache = (time.monotonic()+ttl, address)
        return address
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
        raise DataAdapterError('upstream_schema') from None
