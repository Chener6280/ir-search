"""Fiona configuration, read-only tools and no premature routing changes."""
from dataclasses import replace
import json
import pytest

from ir_search.context import RequestContext, RequestStopped
from ir_search.infrastructure.fiona import FionaProfile, FionaClient, fiona_profile, _RPCResponse
from ir_search.infrastructure.credentials import SourceConfigError, source_configuration_status
from ir_search.registry import DataAdapterError
from ir_search.source_policy import source_policy

PROFILE = FionaProfile('synthetic_fiona_token')


class Transport:
    def __init__(self, hook=None): self.calls=[]; self.hook=hook
    def __call__(self, profile, route, payload, **kwargs):
        self.calls.append((route, payload, kwargs))
        method = payload['method']
        if self.hook:
            out=self.hook(method)
            if out is not None: return out
        result = ({'protocolVersion': '2024-11-05', 'serverInfo': {'name':'fiona-test','version':'1'}} if method=='initialize'
            else {'tools': [{'name':'get_futures_trading_calendar','inputSchema':{'type':'object'}}]} if method=='tools/list'
            else {'content':[{'type':'text','text':'{"total": 2, "returned": 1, "truncated": true}'}],
                  'structuredContent':{'total':2,'returned':1,'truncated':True}})
        return _RPCResponse({'jsonrpc':'2.0','id':payload.get('id'),'result':result},'session_1')


def test_config_alias_normalization_and_no_vendor_key_reuse(tmp_path):
    assert fiona_profile(values={'FIONA_MCP':PROFILE.token}) is None
    assert fiona_profile(values={'FIONA_MCP_ENABLED':'true','FIONA_MCP':'Bearer '+PROFILE.token})==PROFILE
    assert PROFILE.token not in repr(PROFILE)
    with pytest.raises(SourceConfigError): fiona_profile(values={'FIONA_MCP_ENABLED':'true','FMP_API_KEY':PROFILE.token})
    with pytest.raises(SourceConfigError,match='conflicting'):
        fiona_profile(values={'FIONA_MCP_ENABLED':'true','FIONA_MCP_TOKEN':PROFILE.token,'FIONA_MCP':'different_token'})
    p=tmp_path/'c.env';p.write_text('FIONA_MCP_ENABLED=true\nFIONA_MCP='+PROFILE.token);p.chmod(0o600)
    health=source_configuration_status(env_file=p)
    row=next(s for s in health['sources'] if s['provider']=='fiona')
    assert row['configured'] and row['authentication_mode']=='fiona_mcp_bearer'
    assert row['integration_stage']=='get_data_adapter'
    assert PROFILE.token not in json.dumps(health)


@pytest.mark.parametrize('change',[{'FIONA_MCP_ENABLED':'yes'},{'FIONA_MCP':''},
    {'FIONA_MCP':'bad\r\nheader'},{'FIONA_MAX_CALLS_PER_QUERY':'41'},{'FIONA_MAX_CALLS_PER_QUERY':'bad'}])
def test_invalid_configuration(change):
    with pytest.raises(SourceConfigError):
        fiona_profile(values={'FIONA_MCP_ENABLED':'true','FIONA_MCP':PROFILE.token,**change})


def test_handshake_discovery_read_and_truncation_preserved():
    transport=Transport();c=FionaClient(PROFILE,'futures_market_data',transport=transport)
    tools=c.list_tools(context=RequestContext())
    assert len(tools['tools'])==1 and c.server_info['name']=='fiona-test'
    out=c.read('get_futures_trading_calendar',{'start_date':'2026-09-01','end_date':'2026-09-02'},context=RequestContext())
    assert out.result['structuredContent']['truncated'] and out.source_text_trust=='untrusted'
    assert c.tool_calls==1 and c.http_requests==4
    assert [p['method'] for _,p,_ in transport.calls]==['initialize','notifications/initialized','tools/list','tools/call']
    assert transport.calls[-1][2]['session_id']=='session_1'


def test_unknown_routes_or_tools_do_not_send_credentials():
    with pytest.raises(ValueError): FionaClient(PROFILE,'https://evil.example/')
    transport=Transport();c=FionaClient(PROFILE,'options_market_data',transport=transport)
    for tool in ('delete_account','get_futures_snapshot_quotes','generate_report'):
        with pytest.raises(DataAdapterError,match='unsupported'):c.read(tool,{},context=RequestContext())
    assert not transport.calls


def test_cancel_budget_and_no_new_default_route():
    transport=Transport();c=FionaClient(replace(PROFILE,max_calls_per_query=1),'options_market_data',transport=transport)
    ctx=RequestContext();ctx.cancel()
    with pytest.raises(RequestStopped): c.list_tools(context=ctx)
    assert not transport.calls
    c.read('get_option_contract_info',{'date':'2026-09-11','limit':1},context=RequestContext())
    with pytest.raises(DataAdapterError,match='fiona_call_budget_exhausted'):
        c.read('get_option_contract_info',{},context=RequestContext())
    assert all('fiona' not in row['providers'] for row in source_policy()['routes'] if row['dataset'] not in {'derivatives_bars','option_risk'})


def test_echo_and_server_error_not_exposed():
    def hook(method):
        return _RPCResponse({'jsonrpc':'2.0','id':1,'result':{'echo':PROFILE.token}})
    c=FionaClient(PROFILE,'options_market_data',transport=Transport(hook))
    with pytest.raises(DataAdapterError) as exc:c.list_tools(context=RequestContext())
    assert PROFILE.token not in str(exc.value)


def test_vendor_backend_failure_has_safe_diagnostic():
    from ir_search.infrastructure.fiona import _error_code
    assert _error_code(message="Server error '502 Bad Gateway' for private backend")=='network'
    assert _error_code(message="Server error '501 Not Implemented'")=='unsupported'
