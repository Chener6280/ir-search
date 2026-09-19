"""Synthetic audio fixtures; no real credentials, recordings or paid calls."""
from dataclasses import replace
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import socket
import struct
import sys
from types import SimpleNamespace

import pytest

from ir_search import MaterialRequest, MaterialSearchRequest, MaterialRegistry, RequestContext, retrieve, search_materials, build_material_registry
from ir_search.adapters.platform_materials import XiaoyuzhouMaterialAdapter, platform_material_profile
from ir_search.infrastructure import audio_asr as asr, audio_documents as audio, platform_documents as platform
from ir_search.infrastructure.credentials import WebMaterialProfile, SourceConfigError, source_configuration_status
from ir_search.infrastructure.public_web import _Reply
from ir_search.infrastructure.web_search import WebSearchPage
from ir_search.mcp_server import retrieve_payload, search_materials_payload
from ir_search.registry import DataAdapterError
from ir_search.context import RequestStopped

URL = 'https://www.xiaoyuzhoufm.com/episode/1234567890abcdef12345678'
MEDIA = 'https://media.xyzcdn.net/example/episode.mp3'
NOW = datetime(2026, 9, 17, tzinfo=timezone.utc)


def episode_html(**changes):
    e = {'eid': URL.rsplit('/', 1)[-1], 'title': '投研播客', 'shownotes': '<p>贵州茅台动销：节目简介。</p>',
         'duration': 100, 'pubDate': NOW.isoformat(), 'status': 'NORMAL', 'podcast': {'title': '研究员播客'},
         'media': {'size': 10, 'source': {'mode': 'PUBLIC', 'url': MEDIA}}, 'payType': 'FREE', **changes}
    return '<script id="__NEXT_DATA__" type="application/json">' + json.dumps({'props': {'pageProps': {'episode': e}}}) + '</script><div>ignore comments</div>'


@pytest.fixture
def page(monkeypatch):
    def install(**changes):
        monkeypatch.setattr(audio, '_read', lambda *a, **kw: _Reply(200, 'text/html', '', episode_html(**changes).encode(), NOW))
    install()
    return install


def result():
    return {'text': '贵州茅台动销需要验证。', 'transcript_segments': [{'start_ms': 100, 'end_ms': 1500,
        'start_char': 0, 'end_char': len('贵州茅台动销需要验证。')}], 'model': asr.MODEL, 'resource_id': asr.RESOURCE, 'pcm_duration_ms': 2000,
        'provider_final_received': True, 'machine_transcribed': True}


@pytest.mark.parametrize('changes', [{'audio_mode': 'auto'}, {'audio_max_seconds': 181}, {'audio_max_seconds': 0},
    {'audio_max_seconds': True}, {'audio_start_seconds': -1}, {'audio_start_seconds': 2.5}])
def test_window_validation(changes):
    with pytest.raises(ValueError): MaterialRequest('q', [URL], **changes)
    with pytest.raises(ValueError): audio.read_audio_document(URL, context=RequestContext(), **changes)
    assert retrieve_payload('q', [URL], **changes)['status'] == 'error'


def test_configuration_no_secret_or_network(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: pytest.fail('network'))
    values = {'VOLC_ASR_ENABLED': 'true', 'VOLC_ASR_API_KEY': 'synthetic-key'}
    p = asr.audio_profile(values=values, env_file=tmp_path/'c.env')
    assert p.cache_dir == tmp_path/'.local/audio-cache'
    assert 'synthetic-key' not in repr(p)
    assert 'synthetic-key' not in json.dumps(asr.audio_configuration_status(values=values))
    assert asr.audio_profile(values={}) is None
    for changes in ({'VOLC_ASR_ENABLED': 'yes'}, {'VOLC_ASR_API_KEY': ''}, {'VOLC_ASR_PLAN': 'regular'},
                    {'VOLC_ASR_ENDPOINT': 'wss://attacker.test/'}, {'VOLC_ASR_API_KEY': 'key\nvalue'},
                    {'VOLC_ASR_MODEL': 'fake'}, {'VOLC_ASR_RESOURCE_ID': 'paygo'}):
        with pytest.raises(SourceConfigError): asr.audio_profile(values={**values, **changes})
        assert not asr.audio_configuration_status(values={**values, **changes})['configured']
    assert platform_material_profile('xiaoyuzhou', values={}) is None
    with pytest.raises(SourceConfigError): platform_material_profile('xiaoyuzhou', values={'XIAOYUZHOU_MATERIALS_ENABLED': 'true'})
    path=tmp_path/'credentials.env'; path.write_text('XIAOYUZHOU_MATERIALS_ENABLED=true\nBOCHA_API_KEY=synthetic-key\n'); path.chmod(0o600)
    reg=build_material_registry(env_file=path)
    assert [a.name for a in reg.entries()] == ['xiaoyuzhou']
    item=next(s for s in source_configuration_status(env_file=path)['sources'] if s['provider']=='xiaoyuzhou')
    assert not item['audio_recognition']['enabled']


def test_metadata_not_transcript(page, monkeypatch):
    monkeypatch.setattr(audio, '_cached_transcription', lambda *a, **kw: pytest.fail('ASR'))
    doc=audio.read_audio_document(URL, context=RequestContext())
    assert doc.text == '贵州茅台动销：节目简介。' and 'comments' not in doc.text
    assert doc.extra['web_read']['content_origin'] == 'episode_show_notes'
    bundle=retrieve(MaterialRequest('贵州茅台', [URL]))
    assert bundle.materials[0].text_origin == 'source_excerpt'
    assert not bundle.materials[0].read_details['machine_transcribed']
    page(shownotes='', description='')
    bundle=retrieve(MaterialRequest('q', [URL]))
    assert bundle.materials[0].text == '' and not bundle.materials[0].evidence_spans


@pytest.mark.parametrize('changes', [{'eid': 'wrong'}, {'pubDate':'bad'}, {'duration': True}, {'duration': float('nan')},
    {'duration':0}, {'title': []}, {'media': {'size':'too much'}}, {'status': 'DELETED'}])
def test_invalid_episode(page, changes):
    page(**changes)
    with pytest.raises(DataAdapterError, match='upstream_schema'): audio.read_audio_document(URL, context=RequestContext())


def test_restricted_audio_and_no_generic_fallback(page):
    page(isPrivateMedia=True)
    doc=audio.read_audio_document(URL, context=RequestContext())
    assert doc.extra['web_read']['audio_access'] == 'restricted_or_unavailable'
    b=retrieve(MaterialRequest('q',[URL],audio_mode='transcribe'))
    assert not b.materials and b.diagnostics[0].code == 'audio_access_unavailable'
    for url in [URL.replace('/episode/', '/podcast/'), URL+'?token=x', URL.replace('.com/', '.com.attacker.test/')]:
        with pytest.raises(DataAdapterError): platform._platform_url(url)


def test_discovery_and_preview_no_asr(page, monkeypatch):
    monkeypatch.setattr(audio, '_cached_transcription', lambda *a, **kw: pytest.fail('ASR'))
    class Client:
        def search(self, query, *, limit, context):
            return WebSearchPage([{'url':URL,'title':'茅台动销','snippet':'搜索摘要'}],NOW)
    reg=MaterialRegistry();reg.register(XiaoyuzhouMaterialAdapter(WebMaterialProfile(allow_anonymous=True),bocha_client=Client()))
    request=MaterialSearchRequest('茅台动销', providers=['xiaoyuzhou'], material_types=['audio'],
        published_start='2026-09-01', published_end='2026-09-17')
    r=search_materials(request,registry=reg)
    assert len(r.items)==1
    c=r.items[0]['versions'][0]
    assert c['material_type']=='audio' and c['text_scope']=='source_excerpt'
    assert 'show_notes_not_transcript' in c['warnings']
    preview=search_materials(replace(request,dry_run=True),registry=reg)
    assert preview.plan['source_plans'][0]['audio_transcription']=='never_during_search'


def frame(payload, *, flags=3, kind=9, compression=1):
    raw=json.dumps(payload).encode()
    if compression: raw=gzip.compress(raw)
    return bytes([0x11,(kind<<4)|flags,0x10|compression,0])+(struct.pack('>i',-2) if flags&1 else b'')+struct.pack('>I',len(raw))+raw


def test_protocol_roundtrip_and_malformed_frames():
    packet=asr._packet(b'abc',2,audio=True,last=True)
    assert packet[:4] == b'\x11\x23\x01\x00' and struct.unpack('>i',packet[4:8])[0] == -2
    assert gzip.decompress(packet[12:]) == b'abc'
    for flags in (0,1,2,3):
        final,payload=asr._decode(frame({'result': {'text':'音频'}},flags=flags))
        assert final==bool(flags&2) and payload['result']['text']=='音频'
    for value in (b'',b'12345678','not bytes',frame([]),frame({'x':1})[:-1],b'\x11\x93\x12\x00'+b'0'*10,
                  frame({'x':'a'*1100000})):
        with pytest.raises(DataAdapterError):asr._decode(value)
    with pytest.raises(DataAdapterError,match='audio_format_invalid'):asr._decode(frame({'code':45000151}))


def test_recognition_final_text_and_validation(tmp_path, monkeypatch):
    profile=asr.AudioProfile('synthetic-key',tmp_path)
    def stream(*a):return {'result':{'text':'茅台动销','utterances':[{'text':'茅台动销','start_time':0,'end_time':800}]}}
    monkeypatch.setattr(asr,'_stream',stream)
    r=asr.transcribe_pcm(b'\0'*32000,profile=profile,context=RequestContext())
    assert r['text']=='茅台动销' and r['transcript_segments'][0]['end_char']==4
    assert not r['semantic_smoothing']
    with pytest.raises(ValueError):asr.transcribe_pcm(b'x',profile=profile,context=RequestContext())
    with pytest.raises(ValueError):asr.transcribe_pcm(b'xx',profile=None,context=RequestContext())
    with pytest.raises(DataAdapterError,match='audio_time_budget_insufficient'):
        asr.transcribe_pcm(b'\0'*32000,profile=profile,context=RequestContext(timeout_seconds=1))
    for payload,code in [({'text':''},'audio_no_speech'),({'text':'synthetic-key'},'upstream_schema'),
                          ({'utterances':[{'text':'x','start_time':0,'end_time':999999}]},'upstream_schema')]:
        monkeypatch.setattr(asr,'_stream',lambda *a,payload=payload:{'result':payload})
        with pytest.raises(DataAdapterError,match=code):asr.transcribe_pcm(b'\0'*32000,profile=profile,context=RequestContext())


def test_transcript_offsets_and_mcp(page, tmp_path, monkeypatch):
    profile=asr.AudioProfile('synthetic-key',tmp_path)
    monkeypatch.setattr(audio,'audio_profile',lambda:profile)
    text=result()['text'];r=result();r['transcript_segments'][0]['end_char']=len(text)
    r.update(text_hash=hashlib.sha256(text.encode()).hexdigest(),window_start_seconds=10,window_end_seconds=12,
             cache_status='hit',asr_invoked=False,audio_sha256='a'*64,pcm_sha256='b'*64)
    monkeypatch.setattr(audio,'_cached_transcription',lambda *a:dict(r))
    bundle=retrieve(MaterialRequest('贵州茅台', [URL], audio_mode='transcribe',audio_start_seconds=10,audio_max_seconds=2))
    m=bundle.materials[0]
    assert m.text_origin=='asr_transcript' and m.read_details['next_start_seconds']==12
    assert m.read_details['transcript_segments'][0]['start_ms']==10100
    assert 'audio_partial_episode' in m.warnings
    for span in m.evidence_spans:
        assert span['text']==m.text[span['start_char']:span['end_char']]
        assert span['extra']['start_ms']==10100 and span['extra']['machine_transcribed']
    payload=retrieve_payload('贵州茅台',[URL],audio_mode='transcribe',audio_start_seconds=10,audio_max_seconds=2)
    json.dumps(payload)
    assert payload['materials'][0]['text_origin']=='asr_transcript'
    assert not audio._audio_citation({'content_origin':'episode_show_notes'},text,0,2)
    assert not audio._audio_citation({'content_origin':'volcengine_asr','machine_transcribed':True,
                                     'transcript_segments':[{}]},text,0,2)


def test_cache_hit_no_repeat_charge_and_integrity(page, tmp_path, monkeypatch):
    profile=asr.AudioProfile('synthetic-key',tmp_path/'private')
    episode=audio._episode(URL,RequestContext())
    monkeypatch.setitem(sys.modules,'websockets.sync.client',SimpleNamespace(connect=lambda:None))
    monkeypatch.setattr(audio.shutil,'which',lambda _: '/fake/ffmpeg')
    monkeypatch.setattr(audio,'_download',lambda *a:b'audio')
    monkeypatch.setattr(audio,'_pcm',lambda *a:b'\0'*64000)
    calls=[]
    def recognize(*a,**kw):calls.append(1);return result()
    monkeypatch.setattr(audio,'transcribe_pcm',recognize)
    ctx=RequestContext(timeout_seconds=100)
    first=audio._cached_transcription(episode,0,2,profile,'transcribe',ctx)
    second=audio._cached_transcription(episode,0,2,profile,'cache_only',ctx)
    assert len(calls)==1 and first['cache_status']=='miss' and second['cache_status']=='hit'
    assert second['asr_invoked'] is False
    with pytest.raises(DataAdapterError,match='audio_cache_miss'):audio._cached_transcription(episode,2,2,profile,'cache_only',ctx)
    target=next(profile.cache_dir.glob('*.json'));target.write_text('{}')
    with pytest.raises(DataAdapterError,match='audio_cache_invalid'):audio._cached_transcription(episode,0,2,profile,'transcribe',ctx)
    assert len(calls)==1


def batch_cache(monkeypatch, tmp_path, fail_at=None, changed_at=None):
    monkeypatch.setattr(audio,'audio_profile',lambda:asr.AudioProfile('synthetic-key',tmp_path))
    calls=[]
    def cached(episode,start,seconds,profile,mode,context):
        calls.append(start)
        if start==fail_at: raise DataAdapterError('audio_cache_miss')
        r=result();r.update(window_start_seconds=start,window_end_seconds=start+seconds,
            audio_sha256=('c' if start==changed_at else 'a')*64,pcm_sha256='b'*64,
            cache_status='hit',asr_invoked=False,text_hash=hashlib.sha256(r['text'].encode()).hexdigest())
        return r
    monkeypatch.setattr(audio,'_cached_transcription',cached)
    return calls


def test_audio_batch_complete_tail_absolute_citations_and_mcp(page,tmp_path,monkeypatch):
    page(duration=5)
    calls=batch_cache(monkeypatch,tmp_path)
    result=retrieve_payload('贵州茅台',[URL],audio_mode='cache_only',audio_max_seconds=2,audio_window_count=3)
    assert len(result['materials'])==1
    material=result['materials'][0];d=material['read_details']
    assert calls==[0,2,4] and d['windows_completed']==3 and d['whole_episode_transcribed']
    assert d['next_start_seconds'] is None and d['cache_status']=='hit' and not d['asr_invoked']
    assert [r['window_end_seconds'] for r in d['window_manifest']]==[2,4,5]
    assert [r['start_ms'] for r in d['transcript_segments']]==[100,2100,4100]
    assert all(s['text']==material['text'][s['start_char']:s['end_char']] for s in material['evidence_spans'])


@pytest.mark.parametrize('changed',[False,True])
def test_audio_batch_failure_preserves_completed_window_and_resume(page,tmp_path,monkeypatch,changed):
    calls=batch_cache(monkeypatch,tmp_path,**({'changed_at':2} if changed else {'fail_at':2}))
    bundle=retrieve(MaterialRequest('贵州茅台',[URL],audio_mode='cache_only',audio_max_seconds=2,audio_window_count=3))
    d=bundle.materials[0].read_details
    assert calls==[0,2] and d['windows_completed']==1 and d['next_start_seconds']==2
    assert not d['whole_episode_transcribed']
    assert d['window_failure_code']==('audio_source_changed' if changed else 'audio_cache_miss')


@pytest.mark.parametrize('count',[0,6,True,1.5,'2'])
def test_audio_batch_count_validation(count):
    with pytest.raises(ValueError):MaterialRequest('q',[URL],audio_window_count=count)
    assert retrieve_payload('q',[URL],audio_window_count=count)['status']=='error'


@pytest.mark.parametrize('url',[MEDIA.replace('https:','http:'), 'https://127.0.0.1/a.mp3',
    MEDIA+'?token=secret','https://attacker.test/a.mp3',MEDIA.replace('.mp3','.m3u8')])
def test_no_untrusted_media_targets(url):
    with pytest.raises(DataAdapterError):audio._media_url(url)


def test_download_no_cross_host_redirect(monkeypatch):
    monkeypatch.setattr(audio,'_request',lambda *a,**kw:_Reply(302,'','https://attacker.test/a.mp3',b'',NOW))
    with pytest.raises(DataAdapterError,match='blocked_url'):audio._download(MEDIA,RequestContext())


def test_cancel_before_asr(tmp_path):
    ctx=RequestContext();ctx.cancel()
    with pytest.raises(RequestStopped,match='cancelled'):
        asr.transcribe_pcm(b'\0'*32000,profile=asr.AudioProfile('test-key',tmp_path),context=ctx)


@pytest.mark.parametrize('failure,expected', [(None,None),(401,'authentication_failed'),(403,'entitlement_denied'),
    (429,'quota'),(302,'audio_provider_error'),('early','audio_incomplete')])
def test_scoped_tls_stream_and_no_redirect_retry(tmp_path,monkeypatch,failure,expected):
    class InvalidStatus(Exception):
        def __init__(self,status): self.response=SimpleNamespace(status_code=status)
    captures=[];packets=[]
    class Socket:
        def __init__(self,*a): pass
        def settimeout(self,v): pass
        def connect(self,address): assert address==('93.184.216.34',443)
        def shutdown(self,*a): pass
        def close(self): pass
    class WS:
        socket=Socket()
        def send(self,payload):packets.append(payload)
        def recv(self,timeout):return frame({'result':{'text':'测试'}})
        def close(self):pass
    def connect(url,**kw):
        captures.append((url,kw))
        if isinstance(failure,int):raise InvalidStatus(failure)
        return WS()
    monkeypatch.setitem(sys.modules,'websockets.sync.client',SimpleNamespace(connect=connect))
    monkeypatch.setitem(sys.modules,'websockets.exceptions',SimpleNamespace(InvalidStatus=InvalidStatus))
    monkeypatch.setattr(asr,'_resolve',lambda *a:(socket.AF_INET,socket.SOCK_STREAM,0,'',('93.184.216.34',443)))
    monkeypatch.setattr(asr.socket,'socket',Socket)
    profile=asr.AudioProfile('test-key',tmp_path)
    if expected:
        with pytest.raises(DataAdapterError,match=expected):asr._stream(b'\0'*(6402 if failure=='early' else 2),profile,RequestContext())
    else:
        assert asr._stream(b'\0\0',profile,RequestContext())['result']['text']=='测试'
        assert packets[-1][1]==0x23
        config=json.loads(gzip.decompress(packets[0][12:]))
        assert config['audio']['format']=='pcm' and config['request']['model_name']=='bigmodel'
        assert config['request']['enable_ddc'] is False
    assert len(captures)==1
    url,kw=captures[0]
    assert url==asr.ENDPOINT and '/plan/' in url
    assert kw['proxy'] is None and kw['server_hostname']=='openspeech.bytedance.com'
    assert kw['ssl'].check_hostname and kw['ssl'].verify_mode==2
    assert kw['additional_headers']['X-Api-Key']=='test-key'
    assert not kw['logger'].isEnabledFor(10) and not kw['logger'].propagate


def test_real_ffmpeg_bounded_decode_and_cancellation(tmp_path):
    import shutil,wave
    if not shutil.which('ffmpeg'):pytest.skip('Optional FFmpeg unavailable')
    source=tmp_path/'synthetic.wav'
    with wave.open(str(source),'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(b'\0'*64000)
    pcm=audio._pcm(source,'wav',1,1,RequestContext())
    assert len(pcm)==32000
    ctx=RequestContext();ctx.cancel()
    with pytest.raises(RequestStopped):audio._pcm(source,'wav',0,1,ctx)
