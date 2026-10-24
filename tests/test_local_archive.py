import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from ir_search import (index_local_archive, local_archive_status, search_local_archive,
                       read_local_archive, resolve_local_archive_asset, parse_local_archive)


def make_archive(tmp_path):
    root = tmp_path / 'archive'
    (root / 'index').mkdir(parents=True)
    con = sqlite3.connect(root / 'index/archive.sqlite')
    con.executescript('''CREATE TABLE records(provider TEXT,ref TEXT,hash TEXT,day TEXT,payload TEXT);
      CREATE TABLE objects(sha TEXT PRIMARY KEY,path TEXT,size INTEGER,media_type TEXT,first_seen TEXT);
      CREATE TABLE asset_refs(provider TEXT,collection_id TEXT,item_ref TEXT,asset_ref TEXT,sha TEXT,status TEXT,reason TEXT);''')
    base = {'source_item_id': 'wisburg://article/a', 'source_collection': 'article', 'title': '宏观财政',
            'content_text': '宏观财政是供应商摘要，不是原文', 'text_scope': 'abstract',
            'published_on': '2026-09-20', 'fetched_at': '2026-09-22T00:00:00+00:00'}
    for provider, ref, row in [('wisburg', 'wisburg://article/a', base),
             ('ima', 'ima://kb/a', {**base, 'published_on': None, 'title': '未知日期宏观'}),
             ('zsxq', 'zsxq://topic/a', {**base, 'title': '不许复制宏观', 'text_scope': 'metadata_only_group_copy_disabled', 'content_text': 'must not index this secret'})]:
        con.execute('INSERT INTO records VALUES(?,?,?,?,?)', (provider,ref,'hash',None,json.dumps(row)))
    con.commit()
    con.close()
    return root


def add_asset(root, raw, filename='sample.pdf', object_path=None):
    sha = hashlib.sha256(raw).hexdigest()
    relative = object_path or 'objects/' + sha[:2] + '/' + sha
    if object_path is None:
        p = root / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
    with sqlite3.connect(root / 'index/archive.sqlite') as con:
        con.execute('INSERT INTO objects VALUES(?,?,?,?,?)', (sha,relative,len(raw),'application/pdf','now'))
        con.execute('INSERT INTO asset_refs VALUES(?,?,?,?,?,?,?)', ('zsxq','a','zsxq://topic/a','zsxq://file/a',sha,'ok',None))
        row = json.loads(con.execute("SELECT payload FROM records WHERE provider='zsxq'").fetchone()[0])
        row['attachments'] = [{'asset_ref':'zsxq://file/a', 'original_filename':filename}]
        con.execute("UPDATE records SET payload=? WHERE provider='zsxq'", (json.dumps(row),))


def test_index_idempotence_preserves_sources_and_scope(tmp_path):
    root = make_archive(tmp_path)
    source_hash = hashlib.sha256((root/'index/archive.sqlite').read_bytes()).hexdigest()
    a = index_local_archive(root)
    assert a['documents'] == 3
    assert index_local_archive(root)['documents'] == 3
    assert hashlib.sha256((root/'index/archive.sqlite').read_bytes()).hexdigest() == source_hash
    assert search_local_archive(root, query='must not index')['total'] == 0
    assert search_local_archive(root, query='宏观')['total'] == 3
    assert search_local_archive(root, query="' OR 1=1 --")['total'] == 0
    assert search_local_archive(root, query='%')['total'] == 0
    assert search_local_archive(root, start='2026-09-01', end='2026-09-30')['total'] == 2
    assert search_local_archive(root, start='2026-09-01', end='2026-09-30', include_unknown_dates=True)['total'] == 3
    r = search_local_archive(root, source='wisburg')['items'][0]
    assert read_local_archive(root, r['id'])['text_scope'] == 'abstract'
    assert not a['coverage_complete']


def test_paths_checksums_and_atomic_budget_failure(tmp_path):
    root = make_archive(tmp_path)
    add_asset(root, b'fixture bytes', filename='sample.docx')
    index_local_archive(root)
    item = search_local_archive(root, kind='attachment')['items'][0]
    resolved = resolve_local_archive_asset(root, item['id'])
    Path(resolved['path']).write_bytes(b'tampered')
    with pytest.raises(ValueError, match='checksum'):
        resolve_local_archive_asset(root, item['id'])
    with pytest.raises(ValueError, match='budget'):
        index_local_archive(root, max_records=1)
    assert local_archive_status(root)['documents'] == 4
    assert not (root/'derived/local_archive_v1/.writer.lock').exists()
    with pytest.raises(ValueError):
        read_local_archive(root, '../../secret')


def test_external_object_path_is_blocked(tmp_path):
    root = make_archive(tmp_path)
    (tmp_path/'secret').write_bytes(b'secret')
    add_asset(root, b'secret', object_path='../secret')
    index_local_archive(root)
    item = search_local_archive(root, kind='attachment')['items'][0]
    assert item['download_status'] == 'missing_or_invalid'
    with pytest.raises(ValueError):
        resolve_local_archive_asset(root, item['id'])


def test_parser_and_resume_never_require_network(tmp_path, monkeypatch):
    fitz = pytest.importorskip('fitz')
    root = make_archive(tmp_path)
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((40,40),'Macro research 123.45')
    raw = doc.tobytes()
    doc.close()
    add_asset(root, raw)
    index_local_archive(root)
    first = parse_local_archive(root, limit=1)
    assert first['results'][0]['status'] == 'parsed'
    assert first['network_calls'] == 0 and not first['hosted_ocr']
    assert parse_local_archive(root, limit=1)['attempted'] == 0
    r = search_local_archive(root, query='123.45')['items'][0]
    read = read_local_archive(root, r['id'])
    assert read['pages'][0]['page'] == 1
    assert read['parser'] == 'pymupdf'
    assert read['date_basis'] == 'parent_post_date_not_document_date'


def test_public_search_and_retrieve_local_only(tmp_path, monkeypatch):
    import socket
    import ir_search
    from ir_search.adapters.local_archive import LocalArchiveAdapter
    root = make_archive(tmp_path)
    index_local_archive(root)
    monkeypatch.setenv('IR_SEARCH_LOCAL_ARCHIVE_ROOT', str(root))
    monkeypatch.setattr(socket.socket, 'connect', lambda *a: (_ for _ in ()).throw(AssertionError('network')))
    registry = ir_search.MaterialRegistry()
    registry.register(LocalArchiveAdapter(root))
    choices = ir_search.search_materials(ir_search.MaterialSearchRequest('宏观'), registry=registry)
    assert choices.plan['source_calls_started'] == 0
    assert 'providers' in choices.required_inputs
    request = ir_search.MaterialSearchRequest('宏观', keywords=['宏观'], providers=['local_archive'],
                                             published_start='2026-09-01', published_end='2026-09-30')
    result = ir_search.search_materials(request, registry=registry).to_dict()
    assert result['items'], result
    item = search_local_archive(root, source='wisburg')['items'][0]
    bundle = ir_search.retrieve(ir_search.MaterialRequest('宏观', (item['local_ref'],))).to_dict()
    material = bundle['materials'][0]
    assert material['text_origin'] == 'provider_summary'
    assert material['provenance']['generated']
    for span in material['evidence_spans']:
        assert material['text'][span['start_char']:span['end_char']] == span['text']


def test_empty_pdf_not_success_and_office_without_ocr():
    from ir_search.documents.local_parser import parse_bytes
    assert parse_bytes(b'bad pdf', 'x.pdf')['status'] != 'parsed'
    assert parse_bytes(b'<script>alert(1)</script>', 'x.html')['status'] == 'unsupported_format'


def test_derived_symlink_cannot_overwrite_sources(tmp_path):
    root = make_archive(tmp_path)
    source = root / 'index/archive.sqlite'
    before = source.read_bytes()
    derived = root / 'derived/local_archive_v1'
    derived.mkdir(parents=True)
    (derived / 'index.sqlite').symlink_to(source)
    with pytest.raises(ValueError, match='unsafe_derived_symlink'):
        index_local_archive(root)
    assert source.read_bytes() == before
    assert not (derived / '.writer.lock').exists()


def test_cli_json_envelopes_and_date_validation(tmp_path, capsys):
    from ir_search.local_archive_cli import main
    root = make_archive(tmp_path)
    assert main(['--root', str(root), 'index']) == 0
    assert json.loads(capsys.readouterr().out)['result']['documents'] == 3
    assert main(['--root', str(root), 'search', '--start', '2026-09-01']) == 2
    assert json.loads(capsys.readouterr().out)['code'] == 'invalid_archive_dates'
    assert main(['--root', str(root), 'search', '--query', '宏观']) == 0
    assert json.loads(capsys.readouterr().out)['result']['total'] == 3
    assert main(['--root', str(root), 'parse', '--id', '../../secret']) == 2
    assert json.loads(capsys.readouterr().out)['retry_automatically'] is False
