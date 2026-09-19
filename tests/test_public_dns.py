"""YouTube-only encrypted DNS preserves URL and address safety checks."""
import json
from datetime import datetime, timezone
from urllib.parse import urlsplit,parse_qs

import pytest

from ir_search import RequestContext
from ir_search.infrastructure import public_dns, public_web
from ir_search.infrastructure.public_web import _Reply
from ir_search.registry import DataAdapterError


def reply(**changes):
    return dict(Status=0, TC=False, Question=[{'name':'www.youtube.com.','type':1}],
        Answer=[{'name':'www.youtube.com.','type':1,'TTL':30,'data':'142.250.191.206'}], **changes)


def mock_dns(monkeypatch,value):
    calls=[]
    def transport(url,**kwargs):
        calls.append((url,kwargs)); kwargs['context'].begin_operation()
        return _Reply(200,'application/json','',json.dumps(value).encode(),datetime.now(timezone.utc))
    monkeypatch.setattr(public_web,'_request',transport)
    return calls


def test_dns_private_scope_cache_and_no_credentials(monkeypatch):
    calls=mock_dns(monkeypatch,reply());ctx=RequestContext()
    address=public_dns._google_public_address('www.youtube.com',443,ctx)
    assert address[-1]==('142.250.191.206',443)
    assert public_dns._google_public_address('www.youtube.com',443,ctx)==address and len(calls)==1
    url,kwargs=calls[0]
    assert urlsplit(url).hostname=='dns.google'
    assert parse_qs(urlsplit(url).query)['edns_client_subnet']==['0.0.0.0/0']
    assert set(kwargs)=={'context','max_bytes'} and ctx.operations==1
    public_dns._google_public_address('www.youtube.com',443,RequestContext())
    assert len(calls)==2


@pytest.mark.parametrize('ip',['127.0.0.1','10.0.0.1','198.18.0.1','169.254.169.254','::1'])
def test_nonpublic_answers_are_rejected(monkeypatch,ip):
    data=reply();data['Answer'][0]['data']=ip;mock_dns(monkeypatch,data)
    with pytest.raises(DataAdapterError):public_dns._google_public_address('www.youtube.com',443,RequestContext())


@pytest.mark.parametrize('changes',[{'Status':3},{'TC':True},{'Question':[{'name':'evil.test.','type':1}]},
    {'Answer':[]},{'Answer':[{'name':'evil.test.','type':1,'TTL':30,'data':'8.8.8.8'}]}])
def test_dns_response_must_match_question(monkeypatch,changes):
    data=reply();data.update(changes);mock_dns(monkeypatch,data)
    with pytest.raises(DataAdapterError):public_dns._google_public_address('www.youtube.com',443,RequestContext())


def test_cname_chain_and_zero_ttl(monkeypatch):
    data=reply();data['Answer']=[{'name':'www.youtube.com.','type':5,'data':'youtube.l.google.com.'},
        {'name':'youtube.l.google.com.','type':1,'TTL':0,'data':'8.8.8.8'}]
    calls=mock_dns(monkeypatch,data);ctx=RequestContext()
    for _ in range(2):assert public_dns._google_public_address('www.youtube.com',443,ctx)[-1][0]=='8.8.8.8'
    assert len(calls)==2


def test_fixed_endpoint_only_and_default_system(monkeypatch):
    monkeypatch.setattr(public_web,'_resolve',lambda *a:pytest.fail('Unexpected network'))
    for url in ('https://evil.test/','http://www.youtube.com/','https://www.youtube.com:444/'):
        with pytest.raises(DataAdapterError):public_web._request(url,context=RequestContext(),dns_mode='google_doh')
    assert public_dns._youtube_dns_mode({})=='system'
    assert public_dns._youtube_dns_mode({'YOUTUBE_DNS_MODE':'google_doh'})=='google_doh'
    with pytest.raises(DataAdapterError):public_dns._youtube_dns_mode({'YOUTUBE_DNS_MODE':'other'})


def test_source_diagnostics_expose_route_and_cookie_presence_without_values(tmp_path):
    from ir_search.infrastructure.credentials import source_configuration_status
    path=tmp_path/'credentials.env'
    path.write_text('YOUTUBE_DNS_MODE=google_doh\nXUEQIU_COOKIE=synthetic_private_value\nBILIBILI_COOKIE=synthetic_private_value\n')
    path.chmod(0o600)
    result=source_configuration_status(env_file=path)
    video=next(s for s in result['sources'] if s['provider']=='video')
    assert video['youtube_dns_mode']=='google_doh' and video['bilibili_cookie_configured']
    assert 'synthetic_private_value' not in json.dumps(result)
