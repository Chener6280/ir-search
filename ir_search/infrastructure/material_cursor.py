"""Bounded, query-bound scan positions, not authorization or completeness claims."""
from __future__ import annotations

import base64
import hashlib
import json
import re

from ir_search.registry import DataAdapterError

_PROVIDERS = {'zsxq', 'wechat', 'ima', 'xhs'}


def _fingerprint(request, account_scope):
    fields = ('question','symbols','entities','keywords','published_start','published_end',
              'period_start','period_end','material_types')
    raw = json.dumps([account_scope] + [getattr(request,k) for k in fields], default=str,
                     ensure_ascii=False, separators=(',',':'))
    return hashlib.sha256(raw.encode()).hexdigest()


def _decode(token):
    try:
        if not isinstance(token,str) or not 1 <= len(token) <= 12000 or not re.fullmatch(r'[A-Za-z0-9_-]+',token): raise ValueError()
        d = json.loads(base64.urlsafe_b64decode(token+'='*(-len(token)%4)))
        if (not isinstance(d,dict) or set(d) != {'v','provider','binding','collection','query','cursor','offset','digest','seen','page_size'}
                or type(d['v']) is not int or d['v'] != 1 or not isinstance(d['provider'],str) or d['provider'] not in _PROVIDERS
                or not isinstance(d['binding'],str) or not re.fullmatch('[a-f0-9]{64}',d['binding'])
                or any(not isinstance(d[k],str) or len(d[k]) > n for k,n in (('collection',512),('query',2000),('cursor',512),('digest',64)))
                or type(d['offset']) is not int or not 0 <= d['offset'] <= 1000
                or type(d['page_size']) is not int or not 0 <= d['page_size'] <= 30
                or (d['offset'] > 0 and not re.fullmatch('[a-f0-9]{64}',d['digest']))
                or not isinstance(d['seen'],list) or len(d['seen']) > 50
                or any(not isinstance(v,str) or not re.fullmatch('[1-9][0-9]{0,29}',v) for v in d['seen'])): raise ValueError()
        return d
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError): raise DataAdapterError('invalid_cursor') from None


def _states(request, provider, context):
    states = {}
    for token in request.source_cursors:
        d = _decode(token)
        if d['provider'] != provider: continue
        if d['binding'] != _fingerprint(request, context.account_scope): raise DataAdapterError('invalid_cursor')
        key = (d['collection'],d['query'])
        if key in states: raise DataAdapterError('invalid_cursor')
        states[key] = d
    return states


def _digest(rows):
    return hashlib.sha256(json.dumps(rows,sort_keys=True,ensure_ascii=False,separators=(',',':'),default=str).encode()).hexdigest()


def _slice(rows, allowance, state):
    offset = state['offset'] if state else 0
    if offset and (offset >= len(rows) or state['digest'] != _digest(rows)):
        raise DataAdapterError('material_cursor_stale')
    selected = rows[offset:offset+allowance]
    return selected, offset+len(selected)


def _continuation(request, provider, collection, query, cursor, rows, consumed, next_cursor, more, context, *, seen=(), page_size=0):
    if consumed < len(rows):
        offset, digest = consumed, _digest(rows)
    elif more is True and next_cursor and next_cursor != cursor:
        cursor, offset, digest = next_cursor, 0, ''
    else: return None
    d = {'v':1,'provider':provider,'binding':_fingerprint(request,context.account_scope),'collection':collection,
         'query':query,'cursor':cursor,'offset':offset,'digest':digest,'seen':list(seen)[-50:], 'page_size':page_size}
    token = base64.urlsafe_b64encode(json.dumps(d,ensure_ascii=False,separators=(',',':')).encode()).decode().rstrip('=')
    _decode(token)
    return token
