"""Public archival diagnostics remain optional, backward-compatible and secret-free."""
import json
from datetime import datetime, timezone
import pytest

from ir_search import MaterialRegistry, RequestContext, retrieve_asset, list_collection_directory
from ir_search.adapters.ima_materials import IMAMaterialAdapter
from ir_search.infrastructure.credentials import IMAProfile
from ir_search.infrastructure.ima import IMAClient, error_diagnostics
from ir_search.infrastructure.public_web import _Reply
from ir_search.registry import DataAdapterError

PROFILE = IMAProfile('test_api_secret', 'test_client_secret', ('testkb',))


def client(code=765432):
    def wire(*args, **kw):
        kw['context'].begin_operation()
        return _Reply(200, 'application/json', 'https://do-not-persist.invalid',
                      json.dumps({'code': code, 'msg': 'private vendor message', 'data': {}}).encode(),
                      datetime.now(timezone.utc))
    return IMAClient(PROFILE, transport=wire)


def registry(c):
    r = MaterialRegistry(); r.register(IMAMaterialAdapter(PROFILE, client=c)); return r


def test_unknown_code_survives_public_directory_exception_without_vendor_message():
    with pytest.raises(DataAdapterError) as info:
        list_collection_directory('ima', 'testkb', registry=registry(client()))
    exc = info.value
    assert exc.code == 'ima_upstream_rejected'
    assert exc.diagnostics == {'operation': 'get_knowledge_list', 'httpStatus': 200, 'providerCode': 765432}
    assert str(exc) == 'ima_upstream_rejected'
    assert 'private' not in json.dumps(exc.diagnostics)


@pytest.mark.parametrize('code,reason', [(220021, 'ima_daily_quota_exhausted'), (210011, 'entitlement_denied'), (110021, 'rate_limit'), (765432, 'ima_upstream_rejected')])
def test_public_asset_failed_result_preserves_only_allowlisted_fields(code, reason):
    result = retrieve_asset('ima://media/sample', registry=registry(client(code)))
    assert result['status'] == 'failed' and result['reason'] == reason
    assert result['diagnostics'] == {'operation': 'get_media_info', 'httpStatus': 200, 'providerCode': code}
    text = json.dumps(result)
    assert all(s not in text for s in ['private vendor', 'do-not-persist', PROFILE.api_key, PROFILE.client_id])


def test_invalid_diagnostic_values_and_arbitrary_fields_are_dropped():
    e = DataAdapterError('network')
    e.diagnostics = {'operation': 'https://secret', 'providerCode': True, 'httpStatus': 999,
                     'headers': {'Authorization': 'secret'}, 'raw': 'secret'}
    assert error_diagnostics(e) == {}


def test_credential_echo_does_not_get_published_even_in_error_diagnostics():
    def wire(*args, **kw):
        return _Reply(200, 'application/json', '', json.dumps({'code': 765432, 'msg': PROFILE.api_key}).encode(), datetime.now(timezone.utc))
    with pytest.raises(DataAdapterError) as info:
        IMAClient(PROFILE, transport=wire).read('get_media_info', {'media_id': 'sample'}, context=RequestContext())
    assert info.value.code == 'upstream_schema'
    assert info.value.diagnostics == {'operation': 'get_media_info', 'httpStatus': 200}


def test_daily_directory_quota_is_not_permission_denial():
    with pytest.raises(DataAdapterError) as info:
        list_collection_directory('ima', 'testkb', registry=registry(client(220021)))
    assert info.value.code == 'ima_daily_quota_exhausted'
    assert info.value.failure_kind.value == 'quota'
    assert info.value.diagnostics['providerCode'] == 220021
