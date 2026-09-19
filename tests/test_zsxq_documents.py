"""Explicit topic/attachment retrieval and signed-download credential separation."""
from dataclasses import replace
from datetime import datetime, timezone
import json

import pytest

from ir_search import MaterialRequest, RequestContext
from ir_search.infrastructure.credentials import ZsxqProfile
from ir_search.infrastructure.zsxq import ZsxqResponse
from ir_search.infrastructure import zsxq_documents as documents
from ir_search.infrastructure.public_web import _Reply, _url, _request
from ir_search.registry import DataAdapterError
from ir_search.services.retrieval import retrieve

NOW=datetime(2026,9,15,tzinfo=timezone.utc)
PROFILE=ZsxqProfile('synthetic_only_token',('100',))
SIGNED='https://files.zsxq.com/research.pdf?e=123&token=temporary_signature'
REF='zsxq://file/100/101/501'


class Client:
    def __init__(self,**changes):
        self.calls=[]
        self.topic={'topic_id':'101','group':{'group_id':'100','name':'测试星球'},'type':'talk','title':'Research',
                    'content':'Revenue increased with demand.','owner':{'name':'Author'},'create_time':'2026-09-14T10:00:00+0800',
                    'files':[{'file_id':'501','name':'Revenue.pdf','size':1000}]}
        self.topic.update(changes)
    def read(self,tool,args,*,context):
        context.begin_operation();self.calls.append((tool,args))
        return ZsxqResponse({'topic':self.topic} if tool=='get_topic_info' else
                           {'status_code':200,'body':{'succeeded':True,'resp_data':{'download_url':SIGNED}}},NOW)


def fetch(ref='zsxq://topic/100/101',**kw):
    return documents.fetch_zsxq_document(ref,context=RequestContext(),profile=PROFILE,**kw)


def test_explicit_topic_does_not_download_files_and_preserves_attribution():
    client=Client();doc=fetch(client=client,downloader=lambda *a,**k:pytest.fail('hidden download'))
    assert [t for t,_ in client.calls]==['get_topic_info']
    assert doc.text=='Revenue increased with demand.' and doc.extra['sections'][0]['author']=='Author'
    assert doc.extra['attachments'][0]['source_ref']==REF
    assert doc.canonical_url=='https://wx.zsxq.com/group/100/topic/101'


@pytest.mark.parametrize('ref',['zsxq://file/100/101','zsxq://topic/100/101/501','zsxq://topic/100/101?token=x','zsxq://file/100/../501','zsxq://topic/999/101'])
def test_bad_or_unconfigured_scope_references_do_not_use_credentials(ref):
    client=Client()
    with pytest.raises(DataAdapterError):fetch(ref,client=client)
    assert not client.calls


def test_attachment_must_belong_to_authenticated_topic_and_fit_limits():
    client=Client()
    with pytest.raises(DataAdapterError,match='entitlement_denied'):fetch('zsxq://file/100/101/999',client=client)
    assert len(client.calls)==1
    client=Client(files=[{'file_id':'501','name':'Revenue.pdf','size':999999999}])
    with pytest.raises(DataAdapterError,match='response_too_large'):fetch(REF,client=client)
    assert len(client.calls)==1
    with pytest.raises(DataAdapterError,match='upstream_schema'):fetch(client=Client(group={'group_id':'999','name':'wrong'}))


def test_real_pdf_extraction_hides_signed_url_and_keeps_publication_unknown():
    fitz=pytest.importorskip('fitz')
    pdf=fitz.open();page=pdf.new_page();page.insert_text((72,72),'Revenue rose with growing demand.')
    raw=pdf.tobytes();pdf.close()
    calls=[]
    def download(url,**kw):calls.append(url);return raw
    doc=fetch(REF,client=Client(),downloader=download)
    assert calls==[SIGNED] and doc.url==REF and doc.published_at is None
    assert 'Revenue rose' in doc.text and doc.source=='zsxq'
    encoded=json.dumps(doc.to_dict())
    assert 'temporary_signature' not in encoded and PROFILE.token not in encoded and 'files.zsxq.com' not in encoded


def test_missing_pdf_dependency_stops_before_requesting_signed_url(monkeypatch):
    import importlib.util
    monkeypatch.setattr(importlib.util,'find_spec',lambda name:None)
    client=Client()
    with pytest.raises(DataAdapterError,match='dependency_missing'):fetch(REF,client=client)
    assert [tool for tool,_ in client.calls]==['get_topic_info']


def test_malformed_download_payload_is_a_safe_schema_error(monkeypatch):
    import importlib.util
    monkeypatch.setattr(importlib.util,'find_spec',lambda name:object())
    class Malformed(Client):
        def read(self,tool,args,*,context):
            if tool=='call_zsxq_api':
                return ZsxqResponse({'status_code':200,'body':{'succeeded':True,'resp_data':[]}},NOW)
            return super().read(tool,args,context=context)
    with pytest.raises(DataAdapterError,match='upstream_schema'):fetch(REF,client=Malformed())


@pytest.mark.parametrize('redirect',['https://evil.test/a?token=x','http://files.zsxq.com/a','https://127.0.0.1/a','https://files.zsxq.com@evil.test/a'])
def test_signed_download_redirects_remain_public_https_official_host(monkeypatch,redirect):
    calls=[]
    def request(url,**kw):calls.append((url,kw));return _Reply(302,'',redirect,b'',NOW)
    monkeypatch.setattr(documents,'_request',request)
    with pytest.raises(DataAdapterError):documents._download(SIGNED,context=RequestContext())
    assert len(calls)==1 and calls[0][1]['signed_download'] is True and 'headers' not in calls[0][1]


def test_signed_mode_cannot_forward_auth_and_is_not_default():
    with pytest.raises(DataAdapterError,match='blocked_url'):_url(SIGNED)
    assert _url(SIGNED,signed_download=True)[1]=='files.zsxq.com'
    with pytest.raises(DataAdapterError,match='blocked_url'):
        _request(SIGNED,signed_download=True,headers={'Authorization':'secret'},context=RequestContext())
    with pytest.raises(DataAdapterError,match='blocked_url'):
        _request(SIGNED,signed_download=True,method='POST',context=RequestContext())


def test_retrieve_sdk_dispatches_topic_and_preserves_section_quotes(monkeypatch):
    monkeypatch.setattr(documents,'zsxq_profile',lambda:PROFILE)
    monkeypatch.setattr(documents,'ZsxqClient',lambda _:Client())
    result=retrieve(MaterialRequest('Revenue',('zsxq://topic/100/101',))).to_dict()
    assert result['status']=='partial' and result['materials']
    material=result['materials'][0]
    assert material['provenance']['authority']=='ugc' and material['sections'][0]['role']=='post'
    assert material['attachments'][0]['source_ref']==REF
    for span in material['evidence_spans']:
        assert span['text']==material['text'][span['start_char']:span['end_char']]
        assert span['extra']['author']=='Author' and span['page'] is None
    assert SIGNED not in json.dumps(result)
