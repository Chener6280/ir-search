"""Explicit offline ingestion for new collectors; does not mutate legacy archives.

Public functions write only incoming/v1/ under the selected archive root, using
the same single-writer lock as parsing/indexing. Credentials and download URLs
are excluded by an allowlist. Original bytes are content-addressed and atomic.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile

from .local_archive import _inside, _derived, _writer, _key, _url

BASE = 'incoming/v1'
PROVIDERS = {'zsxq', 'ima', 'wisburg'}


def _hash(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def _root(root):
    root = Path(root).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError('invalid_archive_root')
    return root


def _location(root, provider, collection_id, ref):
    if provider not in PROVIDERS or not isinstance(collection_id, str) or not 1 <= len(collection_id) <= 512:
        raise ValueError('invalid_ingest_scope')
    if not isinstance(ref, str) or len(ref) > 2048 or not ref.startswith(provider + '://'):
        raise ValueError('invalid_ingest_ref')
    key = _hash(collection_id + '\n' + ref)
    return _derived(root, BASE + '/records/' + provider + '/' + key + '.json', exists=False)


def _atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read(path):
    if not path.exists():
        return None
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('ingest_metadata_too_large')
    return json.loads(path.read_text(encoding='utf-8'))


def _valid_asset(root, asset):
    try:
        p = _inside(root, asset['object_path'])
        return (asset.get('status') == 'ok' and p.is_file()
                and p.stat().st_size == asset['size_bytes']
                and _file_hash(p) == asset['sha256'])
    except (KeyError, OSError, ValueError):
        return False


def _file_hash(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _index_ref(collection, ref, kind='record'):
    return _hash(collection) + ':' + kind + ':' + ref


def inspect_ingested_record(archive_root, provider, collection_id, ref):
    """Read an incoming record and revalidate successful assets, without network."""
    root = _root(archive_root)
    result = _read(_location(root, provider, collection_id, ref))
    if result:
        for asset in result['attachments']:
            asset['verified'] = _valid_asset(root, asset)
            asset['document_id'] = _key(provider, _index_ref(collection_id, asset['asset_ref'], 'attachment'))
    return result


def _legacy_asset(root, provider, asset_ref):
    db = root / 'index/archive.sqlite'
    if not db.exists():
        return None
    db = _inside(root, 'index/archive.sqlite')
    with sqlite3.connect(db.as_uri() + '?mode=ro', uri=True) as con:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'objects', 'asset_refs'} <= tables:
            return None
        row = con.execute('''SELECT a.sha,o.path,o.size FROM asset_refs a JOIN objects o ON a.sha=o.sha
            WHERE a.provider=? AND a.asset_ref=? AND a.status='ok' LIMIT 1''', (provider, asset_ref)).fetchone()
    if row:
        asset = dict(status='ok', sha256=row[0], object_path=row[1], size_bytes=row[2])
        if _valid_asset(root, asset):
            return asset
    return None


def ingest_local_record(archive_root, provider, collection_id, item):
    """Commit sanitized timeline metadata/text; preserve verified attachment bytes.

    Returns new/updated/unchanged and a local document ID. A changed title/text
    does not invalidate an attachment with the same immutable source asset ref;
    upstream replacement under a stable ref remains an explicit coverage caveat.
    """
    root = _root(archive_root)
    if not isinstance(item, dict):
        raise ValueError('invalid_ingest_item')
    ref = item.get('ref')
    location = _location(root, provider, collection_id, ref)
    scope = item.get('text_scope') or ('abstract' if item.get('summary') else 'metadata')
    text = item.get('text') or item.get('summary') or ''
    if not isinstance(text, str) or len(text) > 100000 or not isinstance(scope, str):
        raise ValueError('invalid_ingest_text')
    if scope.startswith('metadata'):
        text = ''
    title = item.get('title') or ref
    if not isinstance(title, str) or len(title) > 2000:
        raise ValueError('invalid_ingest_title')
    attachments = item.get('attachments') or []
    if not isinstance(attachments, (list, tuple)) or len(attachments) > 50:
        raise ValueError('invalid_ingest_attachments')
    safe = []
    for a in attachments:
        ar = a.get('asset_ref') or a.get('source_ref')
        _location(root, provider, collection_id, ar)
        name = a.get('name') or a.get('original_filename') or ar.rsplit('/', 1)[-1]
        if not isinstance(name, str) or len(name) > 2000:
            raise ValueError('invalid_ingest_filename')
        safe.append({'asset_ref': ar, 'original_filename': name,
                     'media_type': str(a.get('media_type') or 'unknown')[:100],
                     'declared_size': a.get('size_bytes') if type(a.get('size_bytes')) is int else None})
    record = {'schema_version': 1, 'source': provider, 'source_collection': collection_id,
              'source_item_id': ref, 'title': title, 'content_text': text, 'text_scope': scope,
              'published_at': item.get('published_at'), 'updated_at': item.get('updated_at'),
              'source_url': _url(item.get('original_url')), 'attachments': safe,
              'images_discovered': len(item.get('images') or []),
              'original_document_available': item.get('original_document_available'),
              'coverage_complete': False}
    fingerprint = _hash(json.dumps(record, sort_keys=True, ensure_ascii=False))
    with _writer(root):
        old = _read(location)
        existing = {a['asset_ref']: a for a in (old or {}).get('attachments', [])}
        for a in safe:
            prior = existing.get(a['asset_ref'])
            if prior and _valid_asset(root, prior):
                a.update({k: prior[k] for k in ('status', 'sha256', 'object_path', 'size_bytes')})
            else:
                legacy = _legacy_asset(root, provider, a['asset_ref'])
                a.update(legacy or {'status': 'metadata_only'})
        record['fingerprint'] = fingerprint
        record['fetched_at'] = datetime.now(timezone.utc).isoformat()
        record['first_seen_at'] = (old or {}).get('first_seen_at', record['fetched_at'])
        _atomic(location, json.dumps(record, ensure_ascii=False).encode())
    return {'change': 'new' if old is None else 'unchanged' if old.get('fingerprint') == fingerprint else 'updated',
            'document_id': _key(provider, _index_ref(collection_id, ref)),
            'record': record}


def ingest_local_asset(archive_root, provider, collection_id, ref, asset_ref, content):
    """Atomically attach bounded bytes to existing metadata; no URLs/credentials."""
    if not isinstance(content, bytes) or not 0 < len(content) <= 200 * 1024 * 1024:
        raise ValueError('invalid_ingest_bytes')
    root = _root(archive_root)
    location = _location(root, provider, collection_id, ref)
    with _writer(root):
        record = _read(location)
        if not record:
            raise ValueError('ingest_record_missing')
        matching = [a for a in record['attachments'] if a['asset_ref'] == asset_ref]
        if not matching:
            raise ValueError('ingest_asset_not_discovered')
        sha = _hash(content)
        relative = BASE + '/objects/' + sha[:2] + '/' + sha
        target = _derived(root, relative, exists=False)
        if target.exists():
            if target.stat().st_size != len(content) or _file_hash(target) != sha:
                raise ValueError('ingest_object_checksum_mismatch')
        else:
            _atomic(target, content)
        for a in matching:
            a.update(status='ok', sha256=sha, object_path=relative, size_bytes=len(content))
        _atomic(location, json.dumps(record, ensure_ascii=False).encode())
    return {'status': 'ok', 'sha256': sha, 'size_bytes': len(content),
            'document_id': _key(provider, _index_ref(collection_id, asset_ref, 'attachment'))}


def read_ingested_sources(root, max_records):
    """Internal reader for the documented incoming/v1 format, with stable IDs."""
    records, assets = {}, {}
    base = root / BASE / 'records'
    if base.exists():
        for path in sorted(base.glob('*/*.json')):
            if len(records) >= max_records:
                raise ValueError('local_archive_record_budget_exceeded')
            r = _read(_inside(root, path.relative_to(root)))
            source, collection, ref = r['source'], r['source_collection'], r['source_item_id']
            _location(root, source, collection, ref)
            internal = _index_ref(collection, ref)
            records[(source, internal)] = {**r, 'original_ref': ref}
            for a in r['attachments']:
                ar = a['asset_ref']
                key = _index_ref(collection, ar, 'attachment')
                assets[(source, key)] = {**a, 'asset_ref': key, 'original_asset_ref': ar,
                    'source': source, 'source_collection': collection, 'source_record_ref': internal}
    return records, assets
