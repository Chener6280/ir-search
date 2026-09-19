"""Operational status never confuses a configured source with verified content."""
import json
import pytest

from ir_search import diagnose_sources, RequestContext
from ir_search.infrastructure.xhs import XhsProfile, XhsClient
from ir_search.registry import DataAdapterError
from ir_search.services import source_diagnostics as module


def configured(monkeypatch, provider='xhs', **changes):
    row={'provider':provider,'enabled':True,'configured':True,**changes}
    monkeypatch.setattr(module,'source_configuration_status',lambda **k:{'sources':[row],'diagnostics':[]})


def test_default_is_local_and_reports_disabled_without_network(monkeypatch):
    monkeypatch.setattr(XhsClient,'check_login',lambda *a,**k:pytest.fail('No live probe'))
    r=diagnose_sources()
    assert r['source_calls_started']==0 and len(r['sources'])==23
    assert all(not s['search_live_verified'] for s in r['sources'])
    assert next(s for s in r['sources'] if s['provider']=='xhs')['state']=='disabled'
    assert next(s for s in r['sources'] if s['provider']=='fiona')['operations']==['get_data']


def test_dependency_is_local_metadata_not_runtime_verification(monkeypatch):
    configured(monkeypatch,'wind_mysql')
    monkeypatch.setattr(module,'find_spec',lambda _:None)
    r=diagnose_sources(['wind_mysql'])['sources'][0]
    assert r['state']=='dependency_missing' and r['active_backend'] is None
    assert r['diagnostics'][0]['category']=='dependency'
    monkeypatch.setattr(module,'find_spec',lambda _:object())
    r=diagnose_sources(['wind_mysql'])['sources'][0]
    assert r['state']=='configured_unverified' and not r['required_dependencies'][0]['runtime_verified']


def test_optional_dependency_does_not_disable_primary_route(monkeypatch):
    configured(monkeypatch,'wechat');monkeypatch.setattr(module,'find_spec',lambda _:None)
    s=diagnose_sources(['wechat'])['sources'][0]
    assert s['state']=='configured_unverified' and s['optional_dependencies'][0]['installed'] is False
    assert s['active_backend'] is None


def test_live_login_does_not_claim_search_or_body_access(monkeypatch,tmp_path):
    configured(monkeypatch)
    monkeypatch.setattr('ir_search.infrastructure.xhs.xhs_profile',lambda **k:XhsProfile(token='synthetic-private-token',cache_dir=str(tmp_path)))
    calls=[]
    def login(self,*,context):context.begin_operation();calls.append(True);return {'logged_in':True}
    monkeypatch.setattr(XhsClient,'check_login',login)
    r=diagnose_sources(['xhs'],live=True);s=r['sources'][0]
    assert len(calls)==1 and r['source_calls_started']==r['operations_used']==1
    assert s['state']=='login_verified' and s['live_probe']['state']=='passed'
    assert not s['search_live_verified'] and not s['retrieve_live_verified']
    assert 'synthetic-private-token' not in json.dumps(r)


@pytest.mark.parametrize('code',['xhs_login_required','web_content_challenge','rate_limit','timeout'])
def test_live_failures_have_reviewed_actions_without_raw_exceptions(monkeypatch,tmp_path,code):
    configured(monkeypatch)
    monkeypatch.setattr('ir_search.infrastructure.xhs.xhs_profile',lambda **k:XhsProfile(token='synthetic-private-token'))
    def fail(*a,**k):raise DataAdapterError(code)
    monkeypatch.setattr(XhsClient,'check_login',fail)
    s=diagnose_sources(['xhs'],live=True)['sources'][0]
    assert s['state']=='probe_failed' and s['diagnostics'][0]['code']==code
    assert not s['diagnostics'][0]['automatic_retry'] and s['diagnostics'][0]['next_action']


def test_unexpected_probe_error_is_redacted(monkeypatch):
    configured(monkeypatch)
    monkeypatch.setattr('ir_search.infrastructure.xhs.xhs_profile',lambda **k:XhsProfile(token='synthetic-private-token'))
    def fail(*a,**k):raise RuntimeError('private upstream secret')
    monkeypatch.setattr(XhsClient,'check_login',fail)
    r=diagnose_sources(['xhs'],live=True)
    assert 'private upstream secret' not in json.dumps(r)
    assert r['sources'][0]['diagnostics'][0]['code']=='probe_failed'


def test_cancelled_probe_never_connects(monkeypatch):
    configured(monkeypatch)
    monkeypatch.setattr(XhsClient,'check_login',lambda *a,**k:pytest.fail('cancelled'))
    ctx=RequestContext();ctx.cancel()
    r=diagnose_sources(['xhs'],live=True,context=ctx)
    assert r['source_calls_started']==0 and r['sources'][0]['diagnostics'][0]['code']=='cancelled'


def test_unimplemented_live_probe_is_explicit(monkeypatch):
    configured(monkeypatch,'fmp')
    s=diagnose_sources(['fmp'],live=True)['sources'][0]
    assert s['state']=='configured_unverified' and s['live_probe']['state']=='not_probed'
    assert s['diagnostics'][0]['code']=='live_probe_not_implemented'


@pytest.mark.parametrize('providers,live',[([],True),(['unknown'],False),('xhs',False),(['xhs','xhs'],False),([],1)])
def test_invalid_selection(providers,live):
    with pytest.raises(ValueError):diagnose_sources(providers,live=live)


def test_cli_and_mcp_compatibility(capsys):
    assert module.main(['--provider','xhs'])==0
    assert json.loads(capsys.readouterr().out)['source_calls_started']==0
    with pytest.raises(SystemExit):module.main(['--live'])
    from ir_search.mcp_server import source_health_payload
    r=source_health_payload(providers=['xhs'])
    assert 'configured_sources' in r and r['operational_status']['sources'][0]['provider']=='xhs'
    assert source_health_payload(live=True)['status']=='error'
