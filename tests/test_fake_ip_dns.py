"""Exact-host fake-IP compatibility must retain SSRF and TLS protections."""
import copy
import json
import socket
import ssl
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from ir_search import RequestContext
from ir_search.infrastructure import fake_ip_dns as d, public_web as w
from ir_search.registry import DataAdapterError

HOST='money.163.com'
SETTINGS={'PUBLIC_WEB_REAL_DNS_HOSTS':HOST+',www.sse.com.cn',
          'PUBLIC_WEB_REAL_DNS_PROXY':'http://127.0.0.1:7897'}

def answer(ip='183.131.44.95',ttl=60):
    return {'Status':0,'TC':False,'Question':[{'name':HOST,'type':1}],
            'Answer':[{'name':HOST,'type':1,'TTL':ttl,'data':ip}]}

def row(ip):return (socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,443))

def test_exact_hosts_only_no_suffix_or_wildcard_authorization():
    assert d._settings(HOST,SETTINGS)==7897
    assert d._settings('sub.'+HOST,SETTINGS) is None
    assert d._settings('example.com',{}) is None
    with pytest.raises(DataAdapterError,match='source_config_error'):
        d._settings(HOST,dict(SETTINGS,PUBLIC_WEB_REAL_DNS_HOSTS='*.163.com'))

@pytest.mark.parametrize('proxy',['http://8.8.8.8:7897','http://localhost:7897','socks5://127.0.0.1:7897',
    'http://u:p@127.0.0.1:7897','http://127.0.0.1:7897/a','http://127.0.0.1:7897?x=y','http://127.0.0.1'])
def test_proxy_is_explicit_loopback_http_only(proxy):
    with pytest.raises(DataAdapterError,match='source_config_error'):
        d._settings(HOST,dict(SETTINGS,PUBLIC_WEB_REAL_DNS_PROXY=proxy))

def test_public_answer_and_cname_chain():
    doc=answer();doc['Answer']=[{'name':HOST,'type':5,'TTL':30,'data':'cdn.example.net'},
        {'name':'cdn.example.net','type':1,'TTL':60,'data':'183.131.44.95'}]
    rows,ttl=d._parse(json.dumps(doc),HOST,443)
    assert ttl==30 and rows==[row('183.131.44.95')]

@pytest.mark.parametrize('ip',['127.0.0.1','10.0.0.1','169.254.169.254','198.18.0.7'])
def test_real_dns_never_exempts_nonpublic_addresses(ip):
    with pytest.raises(DataAdapterError,match='blocked_url'):d._parse(json.dumps(answer(ip)),HOST,443)

def test_mixed_answers_or_unrelated_owner_fail_closed():
    doc=answer();doc['Answer'].append(dict(doc['Answer'][0],data='127.0.0.1'))
    with pytest.raises(DataAdapterError,match='blocked_url'):d._parse(json.dumps(doc),HOST,443)
    doc=answer();doc['Answer'][0]['name']='unrelated.example'
    with pytest.raises(DataAdapterError,match='blocked_url'):d._parse(json.dumps(doc),HOST,443)

@pytest.mark.parametrize('field,value',[('Status',3),('Status',True),('TC',True),('Question',[]),('Answer',[])])
def test_malformed_truncated_or_mismatched_question_fails(field,value):
    doc=answer();doc[field]=value
    with pytest.raises(DataAdapterError,match='upstream_schema'):d._parse(json.dumps(doc),HOST,443)

def test_duplicate_json_fields_fail():
    raw=json.dumps(answer()).replace('"Status": 0','"Status": 0, "Status": 0')
    with pytest.raises(DataAdapterError,match='upstream_schema'):d._parse(raw,HOST,443)

def test_expired_answer_is_not_pinned_or_cached(monkeypatch):
    monkeypatch.setattr(d,'_query',lambda *args:json.dumps(answer(ttl=1)))
    ticks=iter([0,2]);monkeypatch.setattr(d.time,'monotonic',lambda:next(ticks))
    with pytest.raises(DataAdapterError,match='upstream_schema'):
        d._real_rows(HOST,443,SimpleNamespace(check_active=lambda:None),SETTINGS)

def test_fresh_query_each_time_no_stale_fallback(monkeypatch):
    query=Mock(return_value=json.dumps(answer()));monkeypatch.setattr(d,'_query',query)
    for _ in range(2):assert d._real_rows(HOST,443,RequestContext(),SETTINGS)==[row('183.131.44.95')]
    assert query.call_count==2
    query.side_effect=DataAdapterError('network')
    with pytest.raises(DataAdapterError,match='network'):d._real_rows(HOST,443,RequestContext(),SETTINGS)

def test_original_validator_keeps_numeric_pinning_and_rejects_fake_without_optin(monkeypatch):
    monkeypatch.setattr(w.socket,'getaddrinfo',lambda *a,**k:[row('198.18.0.4')])
    monkeypatch.setattr(d,'_real_rows',lambda *a:None)
    with pytest.raises(DataAdapterError,match='fake_ip_blocked'):w._resolve(HOST,443,RequestContext())
    monkeypatch.setattr(d,'_real_rows',lambda *a:[row('183.131.44.95')])
    assert w._resolve(HOST,443,RequestContext())==row('183.131.44.95')
    monkeypatch.setattr(d,'_real_rows',lambda *a:[row('127.0.0.1')])
    with pytest.raises(DataAdapterError,match='blocked_url'):w._resolve(HOST,443,RequestContext())

def test_nonfake_private_or_mixed_system_answer_never_enters_real_dns(monkeypatch):
    monkeypatch.setattr(d,'_real_rows',lambda *a:pytest.fail('unexpected DoH'))
    for addresses in [[row('127.0.0.1')],[row('198.18.0.4'),row('127.0.0.1')]]:
        monkeypatch.setattr(w.socket,'getaddrinfo',lambda *a,**k:addresses)
        with pytest.raises(DataAdapterError,match='blocked_url'):w._resolve(HOST,443,RequestContext())

def test_doh_uses_verified_tls_fixed_tunnel_no_redirect_or_ambient_auth(monkeypatch):
    body=json.dumps(answer()).encode();calls=[]
    response=SimpleNamespace(status=200,getheader=lambda name,default=None:'application/dns-json' if name=='Content-Type' else default,
        read1=lambda size:chunks.pop(0))
    chunks=[body,b''];sock=Mock()
    conn=SimpleNamespace(sock=sock,set_tunnel=lambda *a:calls.append(('tunnel',a)),
        request=lambda *a,**k:calls.append(('request',a,k)),getresponse=lambda:response,close=Mock())
    def create(host,port,**kwargs):
        assert (host,port)==('127.0.0.1',7897)
        assert kwargs['context'].check_hostname and kwargs['context'].verify_mode==ssl.CERT_REQUIRED
        assert kwargs['context'].minimum_version>=ssl.TLSVersion.TLSv1_2
        return conn
    monkeypatch.setattr(d.http.client,'HTTPSConnection',create)
    context=RequestContext();assert d._query(HOST,7897,context)==body
    assert calls[0]==('tunnel',('cloudflare-dns.com',443))
    assert calls[1][1][0]=='GET' and 'name=money.163.com' in calls[1][1][1]
    assert 'Authorization' not in calls[1][2]['headers']
    assert context.operations==1;conn.close.assert_called_once()
    response.status=302
    with pytest.raises(DataAdapterError,match='network'):d._query(HOST,7897,RequestContext())
