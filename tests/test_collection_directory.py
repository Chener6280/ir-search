from datetime import datetime, timezone

import pytest

from ir_search import list_collection_directory, MaterialRegistry
from ir_search.adapters.ima_materials import IMAMaterialAdapter
from ir_search.infrastructure.credentials import IMAProfile
from ir_search.infrastructure.ima import IMAReply, _validate_parameters
from ir_search.registry import DataAdapterError


def registry(data):
    class Client:
        def read(self, operation, params, *, context):
            assert operation == 'get_knowledge_list'
            _validate_parameters(operation, params)
            context.begin_operation()
            return IMAReply(data, datetime.now(timezone.utc))
    result = MaterialRegistry()
    result.register(IMAMaterialAdapter(IMAProfile('fake_key', 'fake_client', ('kb1',)), client=Client()))
    return result


def test_directory_page_and_folder_ids_without_credentials_or_dates():
    r = list_collection_directory('ima', 'kb1', registry=registry({
        'knowledge_list': [{'media_id': 'folder1', 'title': 'folder', 'media_type': 99},
                           {'media_id': 'doc1', 'title': 'report.pdf', 'media_type': 1, 'url': 'secret'}],
        'is_end': False, 'next_cursor': 'p2'}))
    assert r['items'][0]['folder_id'] == 'folder1'
    assert r['items'][1]['attachments'][0]['asset_ref'] == 'ima://media/doc1'
    assert r['items'][1]['published_at'] is None
    assert r['next_cursor'] == 'p2' and not r['library_complete']
    assert 'secret' not in str(r)


@pytest.mark.parametrize('data', [
    {}, {'knowledge_list': []},
    {'knowledge_list': [], 'is_end': False, 'next_cursor': 'p2'},
    {'knowledge_list': [{'media_id': 'x', 'media_type': 1}], 'is_end': False, 'next_cursor': ''},
    {'knowledge_list': [{'media_id': 'x', 'media_type': 1}], 'is_end': False, 'next_cursor': 'p1'},
])
def test_missing_or_stalled_pagination_is_not_complete(data):
    with pytest.raises(DataAdapterError):
        list_collection_directory('ima', 'kb1', cursor='p1', registry=registry(data))


def test_empty_final_page_and_optional_folder():
    r = list_collection_directory('ima', 'kb1', folder_id='f1', registry=registry({'knowledge_list': [], 'is_end': True}))
    assert not r['has_more'] and r['page_complete']


@pytest.mark.parametrize('params', [
    {'knowledge_base_id': 'kb1', 'cursor': '', 'limit': 21},
    {'knowledge_base_id': 'kb1', 'cursor': '', 'limit': True},
    {'knowledge_base_id': 'kb1', 'cursor': '', 'limit': 20, 'folder_id': '../x'},
    {'knowledge_base_id': 'kb1', 'cursor': '', 'limit': 20, 'query': ''},
])
def test_parameter_allowlist(params):
    with pytest.raises(DataAdapterError):
        _validate_parameters('get_knowledge_list', params)


def test_other_sources_not_silently_routed():
    with pytest.raises(ValueError):
        list_collection_directory('zsxq', 'kb1')
