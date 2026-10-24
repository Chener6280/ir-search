"""Versioned local archive index. Reads source archives; writes derived data only.

No provider calls, LLMs, hosted OCR or credential discovery are used here.
All paths persisted in the index are relative to the selected archive root.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
from urllib.parse import parse_qsl, urlsplit

SCHEMA = 1
DERIVED = 'derived/local_archive_v1'
MAX_TEXT = 8_000_000


def _now():
    return datetime.now(timezone.utc).isoformat()


def _sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def _root(value):
    p = Path(value).expanduser().resolve(strict=True)
    if not p.is_dir() or not ((p / 'index/archive.sqlite').is_file() or (p / 'zsxq_web/groups').is_dir() or (p / 'incoming/v1/records').is_dir()):
        raise ValueError('not_a_supported_archive_root')
    return p


def _inside(root, relative, *, exists=True):
    part = Path(relative)
    if part.is_absolute() or '..' in part.parts:
        raise ValueError('unsafe_archive_path')
    p = (root / part).resolve(strict=exists)
    if root not in p.parents:
        raise ValueError('unsafe_archive_path')
    return p


def _derived(root, relative=DERIVED, *, exists=True):
    # Derived writes must never follow a symlink into source data, even in-root.
    cursor = root
    for part in Path(relative).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError('unsafe_derived_symlink')
    return _inside(root, relative, exists=exists)


def _read_json(p):
    if p.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('archive_metadata_too_large')
    return json.loads(p.read_text(encoding='utf-8'))


def _day(value):
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except (ValueError, TypeError):
        return None


def _url(value):
    if not isinstance(value, str):
        return None
    p = urlsplit(value)
    if p.scheme not in {'http', 'https'} or not p.hostname or p.username or p.password:
        return None
    if any(re.search(r'token|signature|secret|password|api.?key|authorization', k, re.I) for k, _ in parse_qsl(p.query)):
        return None
    return value


def _key(source, ref):
    return _sha(source + '\n' + ref)


def _version_key():
    items = ['local-parser-v1']
    for package in ('PyMuPDF', 'firecrawl-anydoc'):
        try:
            items.append(package + '=' + version(package))
        except PackageNotFoundError:
            items.append(package + '=missing')
    return '|'.join(items)


@contextmanager
def _writer(root):
    folder = _derived(root, exists=False)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = folder / '.writer.lock'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError('local_archive_locked') from None
    try:
        os.write(fd, str(os.getpid()).encode())
        yield folder
    finally:
        os.close(fd)
        lock.unlink()


def _database(root, *, writable=False):
    p = _derived(root, DERIVED + '/index.sqlite', exists=not writable)
    for suffix in ('-journal', '-wal', '-shm'):
        _derived(root, DERIVED + '/index.sqlite' + suffix, exists=False)
    con = sqlite3.connect(p if writable else p.as_uri() + '?mode=ro', uri=not writable, timeout=5)
    con.row_factory = sqlite3.Row
    if writable:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS docs(id TEXT PRIMARY KEY, source TEXT, collection_id TEXT,
          kind TEXT, published_on TEXT, title TEXT, text TEXT, scope TEXT, payload TEXT);
        CREATE INDEX IF NOT EXISTS docs_source_date ON docs(source,published_on);
        CREATE TABLE IF NOT EXISTS parses(cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL);
        ''')
        con.execute('INSERT OR IGNORE INTO meta VALUES(?,?)', ('schema_version', str(SCHEMA)))
        con.commit()
        p.chmod(0o600)
    row = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if not row or row[0] != str(SCHEMA):
        con.close()
        raise ValueError('local_archive_schema_mismatch')
    return con


def _source_records(root, max_records):
    records, asset_names, assets = {}, {}, {}
    source_db = root / 'index/archive.sqlite'
    if source_db.exists():
        source_db = _inside(root, 'index/archive.sqlite')
        with sqlite3.connect(source_db.as_uri() + '?mode=ro', uri=True) as con:
            con.execute('BEGIN')  # one read snapshot, including asset status
            count = con.execute('SELECT count(*) FROM records').fetchone()[0]
            if count > max_records:
                raise ValueError('local_archive_record_budget_exceeded')
            for provider, ref, payload in con.execute('SELECT provider,ref,payload FROM records ORDER BY rowid'):
                r = json.loads(payload)
                r.update(source=provider, source_item_id=ref)
                records[(provider, ref)] = r
                for a in r.get('attachments') or []:
                    if isinstance(a, dict):
                        ar = a.get('asset_ref') or a.get('source_ref')
                        if ar:
                            asset_names[(provider, ref, ar)] = a
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if {'asset_refs', 'objects'} <= tables:
                for provider, coll, ref, ar, sha, status, reason, op, size in con.execute('''
                    SELECT a.provider,a.collection_id,a.item_ref,a.asset_ref,a.sha,a.status,a.reason,o.path,o.size
                    FROM asset_refs a LEFT JOIN objects o ON a.sha=o.sha'''):
                    a = dict(asset_names.get((provider, ref, ar), {}))
                    a.update(asset_ref=ar, source_record_ref=ref, source=provider, source_collection=coll,
                             sha256=sha, status=status, reason=reason, object_path=op, size_bytes=size)
                    assets[(provider, ar)] = a
            # Some older snapshots have no asset_refs table.
            for (provider, ref, ar), a in asset_names.items():
                assets.setdefault((provider, ar), {**a, 'asset_ref': ar, 'source': provider, 'source_record_ref': ref})
    web_root = root / 'zsxq_web/groups'
    if web_root.exists():
        for index, p in enumerate(sorted(web_root.glob('*/topics/*/record.json'))):
            if len(records) >= max_records:
                raise ValueError('local_archive_record_budget_exceeded')
            p = _inside(root, p.relative_to(root))
            r = _read_json(p)
            provider, ref = 'zsxq_web', str(r.get('source_item_id') or p.parent.name)
            r['source'] = provider
            records[(provider, ref)] = r
            for a in r.get('attachments') or []:
                ar = str(a.get('asset_ref') or ref + '/asset/' + _sha(str(a.get('original_filename', ''))))
                a = {**a, 'asset_ref': ar, 'source': provider, 'source_record_ref': ref,
                     'source_collection': r.get('source_collection')}
                if a.get('object_path'):
                    a['object_path'] = str(Path('zsxq_web') / a['object_path'])
                assets[(provider, ar)] = a
    from .local_ingest import read_ingested_sources
    incoming_records, incoming_assets = read_ingested_sources(root, max_records)
    # Replace a legacy view only when both the source ref and collection match.
    # Other collections keep independent provenance; original source files stay intact.
    for (source, _), r in incoming_records.items():
        old = records.get((source, r['original_ref']))
        if old and str(old.get('source_collection') or '') == r['source_collection']:
            records.pop((source, r['original_ref']))
            for key in [k for k, a in assets.items() if k[0] == source and a.get('source_record_ref') == r['original_ref']]:
                assets.pop(key)
    records.update(incoming_records)
    assets.update(incoming_assets)
    if len(records) > max_records:
        raise ValueError('local_archive_record_budget_exceeded')
    return records, assets


def _record(r, source, ref):
    text = str(r.get('content_text') or '')
    raw_scope = str(r.get('text_scope') or 'metadata')
    if raw_scope.startswith('metadata'):
        text, scope = '', 'metadata'
    elif raw_scope in {'abstract', 'search_snippet', 'source_excerpt'}:
        scope = raw_scope
    elif raw_scope in {'extracted_text', 'full_text', 'web_member_view', 'original_text'}:
        scope = 'extracted_text'
    else:
        scope = 'source_excerpt' if text.strip() else 'metadata'
    if not text.strip():
        scope = 'metadata'
    published = _day(r.get('published_on') or r.get('local_date') or r.get('published_at'))
    return {'id': _key(source, ref), 'source': source, 'source_ref': r.get('original_ref') or ref, 'kind': 'record',
            'collection_id': str(r.get('source_collection') or ''),
            'title': str(r.get('title') or ref)[:2000], 'text': text[:MAX_TEXT], 'text_scope': scope,
            'published_on': published, 'date_basis': 'stored_source_publication' if published else 'unknown',
            'fetched_at': r.get('fetched_at'), 'original_url': _url(r.get('source_url') or r.get('original_url')),
            'parse_status': 'source_text' if text else 'metadata_only', 'download_status': None,
            'warnings': ['local_snapshot_not_live_source', 'archive_coverage_not_proven'] +
                        (['provider_summary_not_original'] if scope == 'abstract' else []) +
                        (['stored_text_truncated_at_character_budget'] if len(text) > MAX_TEXT else []) +
                        (['published_date_unknown'] if not published else []), 'pages': []}


def _asset(root, a, parent, con):
    source, ref = a['source'], a['asset_ref']
    row = {'id': _key(source, ref), 'source': source, 'source_ref': a.get('original_asset_ref') or ref, 'kind': 'attachment',
           'parent_id': parent.get('id'), 'parent_ref': a.get('source_record_ref'),
           'collection_id': str(a.get('source_collection') or parent.get('collection_id') or ''),
           'title': str(a.get('original_filename') or a.get('name') or ref.rsplit('/', 1)[-1])[:2000],
           'text': '', 'text_scope': 'metadata', 'published_on': parent.get('published_on'),
           'date_basis': 'parent_post_date_not_document_date' if parent.get('published_on') else 'unknown',
           'fetched_at': parent.get('fetched_at'), 'original_url': parent.get('original_url'),
           'raw_path': a.get('object_path'), 'sha256': a.get('sha256'), 'size_bytes': a.get('size_bytes'),
           'download_status': a.get('status') or 'metadata_only', 'download_reason': a.get('reason'),
           'parse_status': 'not_downloaded', 'warnings': ['local_snapshot_not_live_source', 'archive_coverage_not_proven'], 'pages': []}
    if row['download_status'] == 'ok' and row['raw_path'] and re.fullmatch('[0-9a-f]{64}', row['sha256'] or ''):
        try:
            raw_path = _inside(root, row['raw_path'])
            if not raw_path.is_file() or raw_path.stat().st_size != row['size_bytes']:
                raise ValueError('size_mismatch')
            row['parse_status'] = 'pending'
            cached = con.execute('SELECT payload FROM parses WHERE cache_key=?', (row['sha256'] + ':' + _version_key(),)).fetchone()
            if cached:
                p = json.loads(cached[0])
                row.update(parse_status=p['status'], text=p.get('text', ''), pages=p.get('pages', []),
                           parser=p.get('parser'), parser_version=p.get('parser_version'),
                           unread_image_pages=p.get('unread_image_pages', []))
                row['warnings'].extend(p.get('warnings', []))
                row['text_scope'] = 'extracted_text' if row['text'].strip() else 'metadata'
        except (OSError, ValueError):
            row.update(download_status='missing_or_invalid', parse_status='not_downloaded')
    from .local_audio import is_audio_title, indexed_audio
    if is_audio_title(row['title']):
        row['media_kind'] = 'audio'
        if row['download_status'] == 'ok':
            row.update(indexed_audio(root, row['sha256']))
            if row.get('machine_transcribed'):
                row['warnings'].append('machine_transcript_requires_audio_verification')
                if not row['whole_audio_transcribed']:
                    row['warnings'].append('audio_partial_file')
    return row


def _save_doc(con, r):
    text = r.pop('text')
    r['text_hash'] = _sha(text)
    con.execute('INSERT OR REPLACE INTO docs VALUES(?,?,?,?,?,?,?,?,?)',
                (r['id'], r['source'], r['collection_id'], r['kind'], r['published_on'], r['title'],
                 text, r['text_scope'], json.dumps(r, ensure_ascii=False)))
    r['text'] = text


def index_local_archive(archive_root, *, max_records=200000):
    """Atomically rebuild the derived index, preserving parser caches and originals."""
    if type(max_records) is not int or not 1 <= max_records <= 1_000_000:
        raise ValueError('invalid_record_budget')
    root = _root(archive_root)
    with _writer(root):
        con = _database(root, writable=True)
        try:
            records, assets = _source_records(root, max_records)
            con.execute('BEGIN IMMEDIATE')
            con.execute('DELETE FROM docs')
            parents = {}
            for (source, ref), raw in records.items():
                row = _record(raw, source, ref)
                parents[(source, ref)] = row
                _save_doc(con, row)
            for (source, _), a in assets.items():
                _save_doc(con, _asset(root, a, parents.get((source, a.get('source_record_ref')), {}), con))
            con.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('indexed_at', _now()))
            con.commit()
        finally:
            con.close()
    return local_archive_status(root)


def local_archive_status(archive_root):
    """Read local status only; does not scan subscriptions or contact a provider."""
    root = _root(archive_root)
    result = {'schema_version': SCHEMA, 'status': 'not_indexed', 'coverage_complete': False,
              'network_calls': 0, 'source_text_trust': 'untrusted', 'parser_versions': _version_key()}
    if (root / DERIVED / 'index.sqlite').exists():
        with _database(root) as con:
            indexed_at = dict(con.execute('SELECT key,value FROM meta')).get('indexed_at')
            result.update(status='ready' if indexed_at else 'not_indexed', indexed_at=indexed_at,
                          documents=con.execute('SELECT count(*) FROM docs').fetchone()[0],
                          sources=[dict(r) for r in con.execute('SELECT source,count(*) AS count FROM docs GROUP BY source')],
                          states=[dict(r) for r in con.execute("SELECT kind,json_extract(payload,'$.parse_status') AS state,count(*) AS count FROM docs GROUP BY kind,state")],
                          unknown_dates=con.execute('SELECT count(*) FROM docs WHERE published_on IS NULL').fetchone()[0])
    snapshot = root / 'subscriptions/latest.json'
    if snapshot.exists():
        snap = _read_json(_inside(root, 'subscriptions/latest.json'))
        result['subscription_snapshot'] = {k: snap[k] for k in ('scanned_at', 'generated_at', 'summary') if k in snap}
        result['subscription_snapshot']['is_live_check'] = False
    result['web_jobs'] = []
    for p in sorted((root / 'zsxq_web/jobs').glob('*/manifest.json')):
        m = _read_json(_inside(root, p.relative_to(root)))
        result['web_jobs'].append({k: m.get(k) for k in ('job', 'status', 'finished_at', 'topics_discovered',
              'topics_processed_total', 'oldest_visible_date', 'reached_date_floor', 'all_discovered_processed', 'coverage_complete')})
    return result


def search_local_archive(archive_root, *, query='', source='', collection_id='', start=None, end=None,
                         kind='', limit=30, offset=0, include_unknown_dates=False):
    """Literal, parameterized search; no wildcard injection or inferred entity tags."""
    root = _root(archive_root)
    if not isinstance(query, str) or len(query) > 500 or len(query.split()) > 12 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('invalid_archive_search')
    if type(offset) is not int or not 0 <= offset <= 1_000_000:
        raise ValueError('invalid_archive_offset')
    if bool(start) != bool(end) or start and (not _day(start) or not _day(end) or start > end):
        raise ValueError('invalid_archive_dates')
    clauses, args = [], []
    for column, value in (('source', source), ('collection_id', collection_id), ('kind', kind)):
        if value:
            if column == 'kind' and value == 'audio':
                clauses.append("kind='attachment' AND json_extract(payload,'$.media_kind')='audio'")
                continue
            if not isinstance(value, str) or len(value) > 2000:
                raise ValueError('invalid_archive_filter')
            clauses.append(column + '=?')
            args.append(value)
    for term in query.split()[:12]:
        clauses.append('(instr(lower(title),lower(?))>0 OR instr(lower(text),lower(?))>0)')
        args.extend((term, term))
    if start:
        clauses.append('(published_on BETWEEN ? AND ?' + (' OR published_on IS NULL)' if include_unknown_dates else ')'))
        args.extend((start, end))
    where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
    with _database(root) as con:
        total = con.execute('SELECT count(*) FROM docs' + where, args).fetchone()[0]
        rows = con.execute('SELECT payload,text FROM docs' + where + ' ORDER BY published_on DESC,id LIMIT ? OFFSET ?', [*args, limit, offset]).fetchall()
    items = []
    for row in rows:
        item = json.loads(row['payload'])
        text = row['text']
        pos = max(0, text.casefold().find(query.split()[0].casefold())) if query.split() else 0
        item.update(snippet=text[max(0,pos-50):pos+200], local_ref='localarchive://document/' + item['id'])
        for k in ('raw_path', 'pages'):
            item.pop(k, None)
        items.append(item)
    return {'schema_version': SCHEMA, 'items': items, 'total': total, 'offset': offset,
            'has_more': offset + len(items) < total, 'coverage_complete': False,
            'search_basis': 'literal_local_snapshot', 'unknown_dates_included': not start or include_unknown_dates}


def read_local_archive(archive_root, document_id, *, offset=0, max_chars=20000):
    """Read an indexed text segment, with offsets into the immutable text version."""
    root = _root(archive_root)
    if not isinstance(document_id, str) or not re.fullmatch('[0-9a-f]{64}', document_id):
        raise ValueError('invalid_archive_document_id')
    if type(offset) is not int or offset < 0 or type(max_chars) is not int or not 1 <= max_chars <= 100000:
        raise ValueError('invalid_archive_read_budget')
    with _database(root) as con:
        row = con.execute('SELECT payload,text FROM docs WHERE id=?', (document_id,)).fetchone()
        if not row:
            raise ValueError('archive_document_not_found')
        data = json.loads(row['payload'])
        children = [json.loads(r[0]) for r in con.execute("SELECT payload FROM docs WHERE json_extract(payload,'$.parent_id')=? ORDER BY title", (document_id,))]
    text = row['text']
    data.update(text=text[offset:offset+max_chars], offset=offset, total_chars=len(text),
                has_more=offset+max_chars < len(text), local_ref='localarchive://document/' + document_id,
                children=[{k: v for k, v in c.items() if k not in ('raw_path', 'pages')} for c in children],
                source_text_trust='untrusted')
    data['pages'] = [p for p in data.get('pages', []) if p['end'] > offset and p['start'] < offset+max_chars]
    data.pop('raw_path', None)
    return data


def resolve_local_archive_asset(archive_root, document_id):
    """Resolve a verified object only, never an arbitrary filesystem path."""
    root = _root(archive_root)
    read_local_archive(root, document_id, max_chars=1)  # validates ID
    with _database(root) as con:
        row = json.loads(con.execute('SELECT payload FROM docs WHERE id=?', (document_id,)).fetchone()[0])
    if row['kind'] != 'attachment' or row.get('download_status') != 'ok':
        raise ValueError('archive_asset_not_downloaded')
    if not re.fullmatch(r'(?:(?:zsxq_web|incoming/v1)/)?objects/[0-9a-f]{2}/[0-9a-f]{64}', str(row.get('raw_path', ''))):
        raise ValueError('unsafe_archive_object_path')
    path = _inside(root, row['raw_path'])
    if path.stat().st_size > 200*1024*1024 or _sha(path.read_bytes()) != row['sha256']:
        raise ValueError('archive_asset_checksum_mismatch')
    return {'path': str(path), 'filename': row['title'], 'sha256': row['sha256']}


def parse_local_archive(archive_root, *, limit=20, document_ids=()):
    """Parse a bounded set in isolated timed workers. Existing successes are reused."""
    if type(limit) is not int or not 1 <= limit <= 50 or not isinstance(document_ids, (list, tuple)) or len(document_ids) > 50:
        raise ValueError('invalid_parse_budget')
    if any(not isinstance(i, str) or not re.fullmatch('[0-9a-f]{64}', i) for i in document_ids):
        raise ValueError('invalid_archive_document_id')
    root = _root(archive_root)
    results = []
    with _writer(root) as folder:
        con = _database(root, writable=True)
        try:
            rows = con.execute("SELECT payload FROM docs WHERE kind='attachment' ORDER BY id").fetchall()
            pending = []
            seen = set()
            for record in rows:
                r = json.loads(record[0])
                from .local_audio import is_audio_title
                if is_audio_title(r['title']):
                    continue
                if document_ids and r['id'] not in document_ids:
                    continue
                if r.get('download_status') != 'ok' or r.get('parse_status') not in {'pending', 'parser_missing'}:
                    continue
                if r['sha256'] not in seen:
                    seen.add(r['sha256'])
                    pending.append(r)
            for r in pending[:limit]:
                cache_key = r['sha256'] + ':' + _version_key()
                try:
                    source = resolve_local_archive_asset(root, r['id'])
                    with tempfile.TemporaryDirectory(prefix='parse-', dir=folder) as work:
                        output = Path(work) / 'result.json'
                        package_root = str(Path(__file__).resolve().parents[2])
                        boot = 'import sys;sys.path.insert(0,sys.argv.pop(1));from ir_search.documents.local_parser import main;main()'
                        env = {k: v for k, v in os.environ.items() if k in {'PATH', 'SYSTEMROOT', 'WINDIR', 'LANG', 'TMPDIR', 'TEMP', 'TMP'}}
                        proc = subprocess.run([sys.executable, '-I', '-c', boot, package_root,
                                               source['path'], source['filename'], str(output)],
                                              cwd=work, env=env, capture_output=True, timeout=45)
                        p = _read_json(output) if proc.returncode == 0 and output.exists() else {'status': 'worker_failed', 'text': '', 'pages': []}
                except subprocess.TimeoutExpired:
                    p = {'status': 'timeout', 'text': '', 'pages': []}
                except (OSError, ValueError):
                    p = {'status': 'invalid_object', 'text': '', 'pages': []}
                con.execute('INSERT OR REPLACE INTO parses VALUES(?,?)', (cache_key, json.dumps(p, ensure_ascii=False)))
                con.commit()  # each file survives interruption
                results.append({'id': r['id'], 'sha256': r['sha256'], 'status': p['status']})
        finally:
            con.close()
    status = index_local_archive(root)
    return {'status': 'partial' if any(r['status'] != 'parsed' for r in results) else 'ok',
            'results': results, 'attempted': len(results), 'remaining_after_budget': max(0, len(pending)-len(results)),
            'hosted_ocr': False, 'network_calls': 0, 'archive': status}
