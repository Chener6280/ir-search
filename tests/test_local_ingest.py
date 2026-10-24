import hashlib
from pathlib import Path

import pytest

from ir_search import (ingest_local_record, ingest_local_asset, inspect_ingested_record,
                       index_local_archive, search_local_archive, read_local_archive)


def item(text='known text', scope='source_excerpt'):
    return {'ref': 'zsxq://topic/t1', 'title': 'report', 'text': text, 'text_scope': scope,
            'attachments': [{'asset_ref': 'zsxq://file/f1', 'name': 'report.pdf'}]}


def test_idempotent_ingest_and_index_round_trip(tmp_path):
    first = ingest_local_record(tmp_path, 'zsxq', 'g1', item())
    assert first['change'] == 'new'
    r = ingest_local_asset(tmp_path, 'zsxq', 'g1', item()['ref'], 'zsxq://file/f1', b'%PDF-fixture')
    assert r['sha256'] == hashlib.sha256(b'%PDF-fixture').hexdigest()
    assert ingest_local_record(tmp_path, 'zsxq', 'g1', item())['change'] == 'unchanged'
    assert ingest_local_record(tmp_path, 'zsxq', 'g1', item('updated'))['change'] == 'updated'
    saved = inspect_ingested_record(tmp_path, 'zsxq', 'g1', item()['ref'])
    assert saved['attachments'][0]['verified']
    index_local_archive(tmp_path)
    assert search_local_archive(tmp_path)['total'] == 2
    doc = read_local_archive(tmp_path, first['document_id'])
    assert doc['source_ref'] == item()['ref']
    assert doc['text'] == 'updated'
    assert search_local_archive(tmp_path, kind='attachment')['items'][0]['source_ref'] == 'zsxq://file/f1'
    assert not (tmp_path / 'index/archive.sqlite').exists()


def test_two_collections_keep_provenance_with_same_physical_object(tmp_path):
    for coll in ('g1', 'g2'):
        ingest_local_record(tmp_path, 'zsxq', coll, item())
        ingest_local_asset(tmp_path, 'zsxq', coll, item()['ref'], 'zsxq://file/f1', b'%PDF-fixture')
    index_local_archive(tmp_path)
    assert search_local_archive(tmp_path)['total'] == 4
    assert search_local_archive(tmp_path, collection_id='g1')['total'] == 2
    assert len(list((tmp_path / 'incoming/v1/objects').glob('*/*'))) == 1


def test_bad_content_paths_scope_and_mutual_lock(tmp_path):
    with pytest.raises(ValueError):
        ingest_local_record(tmp_path, 'other', 'g1', item())
    with pytest.raises(ValueError):
        ingest_local_record(tmp_path, 'zsxq', 'g1', {'ref': 'https://evil'})
    ingest_local_record(tmp_path, 'zsxq', 'g1', item('secret', 'metadata_only_copy_disabled'))
    index_local_archive(tmp_path)
    assert search_local_archive(tmp_path, query='secret')['total'] == 0
    with pytest.raises(ValueError, match='not_discovered'):
        ingest_local_asset(tmp_path, 'zsxq', 'g1', item()['ref'], 'zsxq://file/other', b'bad')
    lock = tmp_path / 'derived/local_archive_v1/.writer.lock'
    lock.write_text('existing-owner')
    with pytest.raises(ValueError, match='locked'):
        ingest_local_record(tmp_path, 'zsxq', 'g1', item())
    assert lock.read_text() == 'existing-owner'


def test_symlink_and_corrupted_object_rejected(tmp_path):
    ingest_local_record(tmp_path, 'zsxq', 'g1', item())
    ingest_local_asset(tmp_path, 'zsxq', 'g1', item()['ref'], 'zsxq://file/f1', b'data')
    saved = inspect_ingested_record(tmp_path, 'zsxq', 'g1', item()['ref'])
    target = tmp_path / saved['attachments'][0]['object_path']
    target.write_bytes(b'oops')
    assert not inspect_ingested_record(tmp_path, 'zsxq', 'g1', item()['ref'])['attachments'][0]['verified']
    with pytest.raises(ValueError, match='checksum'):
        ingest_local_asset(tmp_path, 'zsxq', 'g1', item()['ref'], 'zsxq://file/f1', b'data')
    other = tmp_path / 'elsewhere'
    other.mkdir()
    (tmp_path / 'incoming/v1/records/ima').symlink_to(other, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):
        ingest_local_record(tmp_path, 'ima', 'kb1', {'ref': 'ima://media/x'})


def test_source_urls_are_allowlisted_not_signed_downloads(tmp_path):
    r = item()
    r.update(original_url='https://example.com/file?signature=secret', headers={'Authorization': 'secret'})
    saved = ingest_local_record(tmp_path, 'zsxq', 'g1', r)['record']
    assert saved['source_url'] is None and 'headers' not in saved
