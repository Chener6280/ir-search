from datetime import datetime,timezone
from dataclasses import replace
import json
import pytest
from ir_search import DataRequest,DataRegistry,get_data,RequestContext
from ir_search.adapters.global_macro import GlobalMacroAdapter,_catalog
from ir_search.infrastructure.public_web import _Reply
from ir_search.registry import DataAdapterError
NOW=datetime(2026,9,18,tzinfo=timezone.utc)


def run(body,symbol='FRED.CPIAUCSL',**changes):
    calls=[]
    def transport(url,**kwargs):calls.append(url);return _Reply(200,'text/csv','',body,NOW)
    registry=DataRegistry();registry.register(GlobalMacroAdapter(transport=transport))
    return get_data(DataRequest(**dict(dict(dataset='macro_series',market='GLOBAL',symbols=[symbol],start='2025-01-01',end='2025-03-31'),**changes)),registry=registry),calls


def test_fred_native_unit_missing_marker_is_null_not_zero():
    r,c=run(b'observation_date,CPIAUCSL\n2025-01-01,300\n2025-02-01,.\n')
    assert r.status.value=='ok' and r.records[0]['unit']=='index_1982_1984_100' and r.records[1]['value'] is None
    assert r.records[0]['frequency']=='monthly' and 'api_key' not in c[0]


def test_worldbank_country_identity_annual_dates_and_update_separate():
    body=json.dumps([dict(pages=1,total=1,lastupdated='2026-09-01'),[dict(indicator={'id':'NY.GDP.MKTP.KD.ZG'},countryiso3code='CHN',date='2025',value=5)]]).encode()
    r,_=run(body,symbol='WB.CHN.NY.GDP.MKTP.KD.ZG');assert r.records[0]['period']=='2025'
    assert r.records[0]['source_updated_on'].isoformat()=='2026-09-01'
    r,_=run(body.replace(b'CHN',b'USA'),symbol='WB.CHN.NY.GDP.MKTP.KD.ZG');assert not r.records


def test_ecb_unit_and_series_must_match():
    body=b'KEY,TIME_PERIOD,OBS_VALUE,UNIT,UNIT_MULT,OBS_STATUS\nEXR.D.USD.EUR.SP00.A,2025-01-02,1.03,USD,0,A\n'
    r,_=run(body,symbol='ECB.EXR.D.USD.EUR.SP00.A');assert r.records[0]['unit']=='USD_per_EUR'
    r,_=run(body.replace(b',USD,0,',b',USD,3,'),symbol='ECB.EXR.D.USD.EUR.SP00.A');assert not r.records


@pytest.mark.parametrize('body',[b'<html>captcha</html>',b'DATE,WRONG\n2025-01-01,3',b'DATE,CPIAUCSL\n2025-01-01,nan',b'DATE,CPIAUCSL\n2025-01-01,3\n2025-01-01,4'])
def test_invalid_body_and_duplicate_period_not_success(body):
    r,_=run(body);assert not r.records and r.status.value=='error'


@pytest.mark.parametrize('change',[dict(symbol='WB.CN.bad'),dict(symbol='FRED.unknown'),dict(frequency='1d'),dict(as_of='2026-01-01T00:00:00Z'),dict(cursor='no')])
def test_unsupported_contract_without_network(change):
    r,calls=run(b'',**change);assert not r.records and not calls


def test_result_limit_and_partial_policy():
    body=b'DATE,CPIAUCSL\n2025-01-01,3\n2025-02-01,4'
    r,_=run(body,limit=1);assert r.status.value=='partial' and not r.complete
    r,_=run(body,limit=1,allow_partial=False);assert not r.records


def test_mixed_source_failure_does_not_hide_success_or_claim_completeness():
    def transport(url,**kw):
        if 'worldbank' in url:raise DataAdapterError('network')
        return _Reply(200,'text/csv','',b'DATE,CPIAUCSL\n2025-01-01,300',NOW)
    reg=DataRegistry();reg.register(GlobalMacroAdapter(transport=transport))
    r=get_data(DataRequest('macro_series',market='GLOBAL',symbols=['FRED.CPIAUCSL','WB.USA.NY.GDP.MKTP.KD.ZG'],start='2025-01-01',end='2025-02-01'),registry=reg)
    assert r.status.value=='partial' and len(r.records)==1
    assert ('FRED','GDPC1') in _catalog()


def test_source_registration_and_packaged_catalog_are_offline(tmp_path,monkeypatch):
    from ir_search import build_data_registry,list_capabilities,diagnose_sources
    from ir_search.material_registry import build_material_registry
    p=tmp_path/'sources.env';p.write_text('GLOBAL_MACRO_ENABLED=true\nHKEX_ENABLED=true\nCOMPANY_IR_ENABLED=true\nFIONA_MCP_ENABLED=true\nFIONA_MCP_TOKEN=synthetic-fiona-secret\n');p.chmod(0o600)
    monkeypatch.setenv('IR_SEARCH_CREDENTIALS_FILE',str(p))
    r=build_data_registry();assert {c.provider for c,a in r.entries()}=={'global_macro','fiona'}
    assert {a.name for a in build_material_registry().entries()}=={'hkex','company_ir'}
    assert len(list_capabilities()['macro_series_catalog']['series'])==11
    report=diagnose_sources(['global_macro','hkex','company_ir','fiona']);assert report['source_calls_started']==0
    assert 'synthetic-fiona-secret' not in json.dumps(report)


def test_fred_uses_runtime_standard_client_header_for_csv_compatibility():
    seen=[]
    def send(url,**kwargs):
        seen.append(kwargs['headers']);return _Reply(200,'text/csv','',b'DATE,CPIAUCSL\n2025-01-01,300',NOW)
    a=GlobalMacroAdapter(transport=send)
    a.query_data(DataRequest('macro_series',market='GLOBAL',symbols=['FRED.CPIAUCSL'],start='2025-01-01',end='2025-01-01'),context=RequestContext())
    assert seen[0]['User-Agent'].startswith('Python-urllib/')
