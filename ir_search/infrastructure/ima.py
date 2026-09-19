"""Official IMA read-only OpenAPI protocol, with bounded, secret-free failures."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
import re
from urllib.parse import quote, unquote, urlsplit

from ir_search.context import RequestStopped
from ir_search.infrastructure.public_web import _request
from ir_search.registry import DataAdapterError

_READS = {
    'search_knowledge_base': 'wiki', 'get_knowledge_base': 'wiki',
    'search_knowledge': 'wiki', 'get_media_info': 'wiki',
    'search_note': 'note', 'get_doc_content': 'note',
}
_ERRORS = {110001:'upstream_schema', 110002:'source_config_error', 110010:'network',
           110020:'entitlement_denied', 110021:'rate_limit', 110030:'entitlement_denied',
           210001:'upstream_schema', 210002:'authentication_failed', 210003:'network',
           210004:'quota', 210005:'entitlement_denied', 210006:'not_found',
           210011:'entitlement_denied', 210012:'not_found', 220030:'entitlement_denied'}


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_+=.-]{1,512}', value):
        raise DataAdapterError('upstream_schema')
    return value


def _ref(kind, identifier):
    if kind not in {'media', 'note'}: raise DataAdapterError('unsupported')
    return 'ima://' + kind + '/' + quote(_id(identifier), safe='')


def _parse_ref(reference):
    try:
        p = urlsplit(reference)
        if (p.scheme != 'ima' or p.netloc not in {'media','note'} or p.query or p.fragment
                or not p.path.startswith('/') or '/' in p.path[1:]):
            raise ValueError()
        identifier = _id(unquote(p.path[1:]))
        if _ref(p.netloc, identifier) != reference: raise ValueError()
        return p.netloc, identifier
    except (ValueError, TypeError, AttributeError, DataAdapterError):
        raise DataAdapterError('unsupported') from None


@dataclass(frozen=True)
class IMAReply:
    data: dict = field(repr=False)
    fetched_at: datetime


class IMAClient:
    def __init__(self, profile, *, transport=None):
        self._profile = profile
        self._transport = transport or _request

    def read(self, operation, parameters, *, context):
        """Read an allowlisted endpoint; API keys never follow redirects or enter errors."""
        if operation not in _READS: raise DataAdapterError('unsupported')
        if context.account_scope != 'default': raise DataAdapterError('entitlement_denied')
        _validate_parameters(operation, parameters)
        context.check_active()
        try:
            reply = self._transport('https://ima.qq.com/openapi/'+_READS[operation]+'/v1/'+operation,
                context=context, method='POST', body=json.dumps(parameters,ensure_ascii=False).encode('utf-8'),
                headers={'Content-Type':'application/json','ima-openapi-clientid':self._profile.client_id,
                         'ima-openapi-apikey':self._profile.api_key,'ima-openapi-ctx':'skill_version=1.1.10'},
                max_bytes=2*1024*1024)
            context.check_active()
            if reply.status != 200: raise DataAdapterError('blocked_url')
            if len(reply.body)>2*1024*1024: raise DataAdapterError('response_too_large')
            # Refuse credential echoes even on successful responses. Do not publish upstream msg.
            raw = reply.body.decode('utf-8')
            if any(secret in raw for secret in (self._profile.api_key,self._profile.client_id)):
                raise DataAdapterError('upstream_schema')
            obj=json.loads(raw)
            if not isinstance(obj,dict) or type(obj.get('code')) is not int: raise DataAdapterError('upstream_schema')
            if obj['code'] != 0: raise DataAdapterError(_ERRORS.get(obj['code'],'ima_upstream_rejected'))
            if not isinstance(obj.get('data'),dict): raise DataAdapterError('upstream_schema')
            # Escaped JSON strings can conceal a credential echo in the wire encoding.
            normalized=json.dumps(obj['data'],ensure_ascii=False)
            if any(secret in normalized for secret in (self._profile.api_key,self._profile.client_id)):
                raise DataAdapterError('upstream_schema')
            return IMAReply(obj['data'], reply.fetched_at)
        except (DataAdapterError,RequestStopped): raise
        except (ValueError,TypeError,UnicodeError,AttributeError):
            raise DataAdapterError('upstream_schema') from None
        except Exception:
            raise DataAdapterError('network') from None


def _validate_parameters(operation, p):
    if not isinstance(p,dict): raise DataAdapterError('unsupported')
    fields={'search_knowledge_base':{'query','cursor','limit'},'get_knowledge_base':{'ids'},
            'search_knowledge':{'query','knowledge_base_id','cursor'},'get_media_info':{'media_id'},
            'search_note':{'search_type','sort_type','query_info','start','end'},
            'get_doc_content':{'note_id','target_content_format'}}[operation]
    if set(p)!=fields: raise DataAdapterError('unsupported')
    if 'query' in p and (not isinstance(p['query'],str) or len(p['query'])>1000): raise DataAdapterError('unsupported')
    if 'cursor' in p and (not isinstance(p['cursor'],str) or len(p['cursor'])>512): raise DataAdapterError('invalid_cursor')
    for key in ('knowledge_base_id','media_id','note_id'):
        if key in p: _id(p[key])
    if operation=='search_knowledge_base' and (type(p['limit']) is not int or not 1<=p['limit']<=20): raise DataAdapterError('unsupported')
    if operation=='get_knowledge_base':
        if not isinstance(p['ids'],list) or not 1<=len(p['ids'])<=20: raise DataAdapterError('unsupported')
        for identifier in p['ids']: _id(identifier)
    if operation=='get_doc_content' and (type(p['target_content_format']) is not int or p['target_content_format']!=0):
        raise DataAdapterError('unsupported')
    if operation=='search_note':
        if (type(p['start']) is not int or type(p['end']) is not int or not 0<=p['start']<p['end']<=1000
                or p['end']-p['start']>20 or type(p['search_type']) is not int or p['search_type']!=1
                or type(p['sort_type']) is not int or p['sort_type']!=0 or not isinstance(p['query_info'],dict)
                or set(p['query_info'])!={'content'} or not isinstance(p['query_info']['content'],str)
                or not 1<=len(p['query_info']['content'])<=1000): raise DataAdapterError('unsupported')


def _page(data, key):
    rows=data.get(key, [])
    if not isinstance(rows,list) or len(rows)>1000 or any(not isinstance(r,dict) for r in rows):
        raise DataAdapterError('upstream_schema')
    end=data.get('is_end')
    cursor=data.get('next_cursor') or None
    if end is not None and type(end) is not bool: raise DataAdapterError('upstream_schema')
    if cursor is not None and (not isinstance(cursor,str) or len(cursor)>512): raise DataAdapterError('upstream_schema')
    # Live search_knowledge may omit pagination flags. Absence does not mean complete.
    return rows, (not end if end is not None else None), cursor
