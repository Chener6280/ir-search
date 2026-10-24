"""One official IMA folder page; the caller owns a bounded recursive walk.

No dates are invented and neither empty search nor keyword expansion is used.
Only sanitized metadata leaves this boundary. Pagination completeness is explicit.
"""
from ir_search.infrastructure.ima import _id, _page, _ref
from ir_search.registry import DataAdapterError
from .collection_export import _adapter, _client, _context, _registry


_TYPES = {1: 'pdf', 2: 'webpage', 3: 'docx', 4: 'pptx', 5: 'xlsx',
          6: 'wechat', 7: 'txt', 9: 'image', 11: 'note', 12: 'audio',
          13: 'txt', 14: 'document', 15: 'video', 16: 'conversation',
          20: 'html', 21: 'document', 99: 'folder'}


def list_collection_directory(provider, collection_id, *, folder_id=None, cursor='',
                              limit=20, context=None, registry=None):
    """Read one IMA knowledge-list page (not personal notes, not a whole library).

    folder_id is returned verbatim for folders; use it on a subsequent explicit
    call. next_cursor is folder-local. Unknown/missing pagination never means end.
    Exceptions preserve existing public DataAdapterError / RequestStopped codes.
    """
    if provider != 'ima':
        raise ValueError('collection_directory_provider_unsupported')
    _id(collection_id)
    params = {'knowledge_base_id': collection_id, 'cursor': cursor, 'limit': limit}
    if folder_id is not None:
        _id(folder_id)
        params['folder_id'] = folder_id
    from ir_search.infrastructure.ima import _validate_parameters
    _validate_parameters('get_knowledge_list', params)
    ctx = _context(context)
    client = _client(_adapter(_registry(registry), provider, ctx), provider)
    reply = client.read('get_knowledge_list', params, context=ctx)
    if 'knowledge_list' not in reply.data:
        raise DataAdapterError('upstream_schema')
    rows, more, following = _page(reply.data, 'knowledge_list')
    if len(rows) > limit:
        raise DataAdapterError('upstream_schema')
    if more is None or (more and (not following or following == cursor or not rows)):
        raise DataAdapterError('upstream_schema')
    items = []
    for row in rows:
        kind = _TYPES.get(row.get('media_type'), 'unknown')
        mid = _id(row.get('media_id') or (row.get('folder_id') if kind == 'folder' else None))
        title = row.get('title') or row.get('file_name') or mid
        if not isinstance(title, str) or not title.strip() or len(title) > 2000:
            raise DataAdapterError('upstream_schema')
        item = {'ref': _ref('media', mid), 'collection_id': collection_id,
                'title': title, 'type': kind, 'published_at': None,
                'text_scope': 'metadata', 'attachments': [],
                'warnings': ['ima_publication_dates_not_provided', 'directory_metadata_not_file_verification']}
        if kind == 'folder':
            item['folder_id'] = mid
        elif kind not in {'webpage', 'wechat', 'note', 'conversation', 'unknown'}:
            item['attachments'] = [{'asset_ref': item['ref'], 'name': title, 'media_type': kind}]
        items.append(item)
    return {'schema_version': '1.0', 'provider': provider, 'collection_id': collection_id,
            'folder_id': folder_id, 'items': items, 'has_more': more,
            'next_cursor': following if more else None, 'page_complete': True,
            'library_complete': False, 'fetched_at': reply.fetched_at.isoformat(),
            'diagnostics': [{'code': 'ima_publication_dates_not_provided'}]}
