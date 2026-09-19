"""The user owns material source selection; configuration is not consent to dispatch."""
import asyncio
import json
import socket

import pytest

from ir_search import MaterialSearchRequest, RequestContext, search_materials, mcp_server
from test_material_search import Source, registry, request


@pytest.mark.parametrize('preview', [False, True])
@pytest.mark.parametrize('excluded', [[], ['source_a']])
def test_empty_selection_returns_options_without_network_or_budget_use(monkeypatch, preview, excluded):
    a,b,private=Source(),Source('source_b'),Source('private_source',account_scope='other')
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:pytest.fail('selection must not resolve DNS'))
    context=RequestContext()
    result=search_materials(request(providers=[],exclude_providers=excluded,dry_run=preview),
                            registry=registry(a,b,private),context=context)
    assert result.required_inputs==['providers']
    assert result.plan['source_selection_policy']=='explicit_user_choice'
    assert result.plan['selected_providers']==[] and result.plan['source_calls_started']==0
    options=result.plan['source_options']
    assert [v['provider'] for v in options]==['source_a','source_b']
    assert options[0]['excluded_by_request']==bool(excluded)
    assert all(v['verification']=='registration_only_not_live_probe' for v in options)
    assert not a.calls and not b.calls and not private.calls and context.operations==0
    assert not result.items and not result.fallback_requests and result.status.value=='unavailable'
    json.dumps(result.to_dict(),allow_nan=False)


def test_user_order_and_budget_are_preserved_without_substitution():
    a,b=Source(),Source('source_b')
    result=search_materials(request(providers=['source_b','source_a'],max_sources=1),registry=registry(a,b))
    assert result.plan['selected_providers']==['source_b']
    assert b.calls and not a.calls and result.coverage[1]['state']=='source_budget_exhausted'
    missing=search_materials(request(providers=['not_installed']),registry=registry(a,b))
    assert missing.coverage[0]['state']=='not_registered' and not missing.items
    assert not a.calls and len(b.calls)==1


def test_missing_selection_and_other_required_scope_are_reported_together():
    out=search_materials(MaterialSearchRequest('贵州茅台9月动销'),registry=registry())
    assert set(out.required_inputs)=={'providers','published_start','published_end','period_start','period_end'}
    assert out.plan['source_options']==[] and 'web' in out.plan['unregistered_providers']


def test_mcp_returns_source_choices_without_dispatch_and_instruction_is_explicit():
    source=Source()
    out=mcp_server.search_materials_payload(request(providers=[]).to_dict(),registry=registry(source))
    assert out['required_inputs']==['providers'] and out['plan']['source_options'][0]['provider']=='source_a'
    assert not source.calls and 'chosen by the user' in mcp_server.MCP_INSTRUCTIONS


def test_actual_mcp_runtime_preserves_missing_provider_handoff(monkeypatch):
    runtime=pytest.importorskip('mcp.server.fastmcp')
    source=Source()
    from ir_search.services import material_search
    monkeypatch.setattr(material_search,'build_material_registry',lambda:registry(source))
    server=runtime.FastMCP('selection');server.run=lambda:None
    monkeypatch.setattr(mcp_server,'make_fastmcp',lambda _:server)
    mcp_server.run()
    async def check():
        response=await server.call_tool('search_materials',request(providers=[]).to_dict())
        out=json.loads(response[0].text)
        assert out['required_inputs']==['providers'] and out['plan']['source_calls_started']==0
    asyncio.run(check())
    assert not source.calls
