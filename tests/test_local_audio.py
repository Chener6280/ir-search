import json
import math
from pathlib import Path

import pytest

from ir_search import (index_local_archive, search_local_archive, read_local_archive,
    parse_local_archive, list_local_audio, prepare_local_audio, transcribe_local_audio, RequestContext)
from ir_search.services import local_audio as audio
from ir_search.infrastructure.audio_asr import AudioProfile
from ir_search.registry import DataAdapterError
from test_local_archive import make_archive, add_asset


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    pytest.importorskip('websockets.sync.client')
    root = make_archive(tmp_path)
    add_asset(root, b'fake local MP3', filename='test.mp3')
    index_local_archive(root)
    item = search_local_archive(root, kind='attachment')['items'][0]
    monkeypatch.setattr(audio.shutil, 'which', lambda _: '/synthetic/ffmpeg')
    monkeypatch.setattr(audio.subprocess, 'run', lambda *a, **k: type('Reply', (), {'stdout': b'{"format":{"duration":"3"}}'})())
    monkeypatch.setattr(audio, '_pcm', lambda *a: b'\0'*96000)
    monkeypatch.setattr(audio.audio_asr, 'audio_profile', lambda: AudioProfile('synthetic-private', tmp_path))
    calls = []
    def recognize(pcm, **kwargs):
        calls.append(len(pcm))
        return {'text': '测试音频 123。', 'transcript_segments': [{'start_char': 0, 'end_char': 9, 'start_ms': 0, 'end_ms': 500}],
                'provider_final_received': True, 'machine_transcribed': True}
    monkeypatch.setattr(audio.audio_asr, 'transcribe_pcm', recognize)
    return root, item['id'], calls


def test_audio_is_separate_and_never_implicitly_transcribed(fixture):
    root, identifier, calls = fixture
    queue = list_local_audio(root)
    assert queue['counts']['downloaded'] == 1 and queue['network_calls'] == 0
    assert parse_local_archive(root, limit=1)['attempted'] == 0
    assert read_local_archive(root, identifier)['parse_status'] == 'audio_pending'
    assert prepare_local_audio(root, identifier)['duration_seconds'] == 3
    assert not calls
    with pytest.raises(ValueError, match='audio_network_not_authorized'):
        transcribe_local_audio(root, identifier)
    assert not calls


def test_live_activity_only_during_owned_inflight_call(fixture, monkeypatch):
    root, identifier, calls = fixture
    recognize = audio.audio_asr.transcribe_pcm
    observed = []
    def during(pcm, **kwargs):
        observed.extend(list_local_audio(root)['activity'])
        return recognize(pcm, **kwargs)
    monkeypatch.setattr(audio.audio_asr, 'transcribe_pcm', during)
    transcribe_local_audio(root, identifier, max_seconds=1, allow_network=True)
    assert observed[0]['document_id'] == identifier
    assert observed[0]['completed_seconds'] == 0
    # A partial transcript is NOT an active paid call between windows.
    assert list_local_audio(root)['activity'] == []


def test_stale_inflight_cannot_claim_transcribing(fixture, monkeypatch):
    root, identifier, calls = fixture
    prepared = prepare_local_audio(root, identifier)
    file = root / prepared['transcript_path']
    value = json.loads(file.read_text())
    value['status'] = 'transcribing'
    value['in_flight'] = {'pid': audio.os.getpid(), 'started_at': audio._now(), 'start_sample': 0, 'end_sample': 16000}
    file.write_text(json.dumps(value))
    assert list_local_audio(root)['activity'] == []  # no owned writer lock


def test_resume_complete_and_idempotent_no_repeat_charge(fixture):
    root, identifier, calls = fixture
    a = transcribe_local_audio(root, identifier, max_seconds=2, allow_network=True)
    assert a['completed_seconds'] == 2 and not a['whole_audio_transcribed']
    b = transcribe_local_audio(root, identifier, max_seconds=2, allow_network=True)
    assert b['whole_audio_transcribed'] and b['completed_seconds'] == 3
    c = transcribe_local_audio(root, identifier, max_seconds=2, allow_network=True)
    assert c['asr_invoked'] is False and len(calls) == 2
    assert len(b['window_timings']) == 2 and b['asr_started_at'] and b['asr_finished_at']
    row = read_local_archive(root, identifier)
    assert row['parse_status'] == 'audio_transcribed' and row['machine_transcribed']
    assert row['transcript_segments'][1]['start_ms'] == 2000
    assert list_local_audio(root)['counts']['transcribed'] == 1
    assert (root / b['text_path']).read_text() == row['text']


def test_transcript_search_and_time_citation(fixture, monkeypatch):
    import ir_search
    root, identifier, calls = fixture
    transcribe_local_audio(root, identifier, max_seconds=1, allow_network=True)
    monkeypatch.setenv('IR_SEARCH_LOCAL_ARCHIVE_ROOT', str(root))
    matches = search_local_archive(root, kind='audio', query='123')
    assert matches['total'] == 1
    material = ir_search.retrieve(ir_search.MaterialRequest('123', (matches['items'][0]['local_ref'],))).to_dict()['materials'][0]
    assert material['text_origin'] == 'asr_transcript'
    assert material['read_details']['whole_audio_transcribed'] is False
    assert material['evidence_spans'][0]['extra']['start_ms'] == 0
    assert material['evidence_spans'][0]['text'] == material['text']


def test_failed_attempt_not_automatically_retried(fixture, monkeypatch):
    root, identifier, calls = fixture
    def fail(*args, **kwargs):
        calls.append('failed')
        exc = DataAdapterError('quota')
        exc.response_metrics = {'frames_received': 42, 'response_bytes_received': 1000,
                                'unexpected_secret': 'never persist', 'pcm_bytes_sent': 'not an integer'}
        raise exc
    monkeypatch.setattr(audio.audio_asr, 'transcribe_pcm', fail)
    with pytest.raises(DataAdapterError, match='quota'):
        transcribe_local_audio(root, identifier, max_seconds=1, allow_network=True)
    with pytest.raises(DataAdapterError, match='audio_retry_requires_confirmation'):
        transcribe_local_audio(root, identifier, max_seconds=1, allow_network=True)
    assert calls == ['failed']
    prepared = prepare_local_audio(root, identifier)
    assert prepared['audio_status'] == 'failed'
    assert prepared['failed_asr_elapsed_seconds'] >= 0
    saved = json.loads((root / prepared['transcript_path']).read_text())
    assert saved['failed_attempts'][0]['response_metrics'] == {'frames_received': 42, 'response_bytes_received': 1000}


def test_authorized_retry_keeps_failure_timing_and_resumes(fixture, monkeypatch):
    root, identifier, calls = fixture
    recognize = audio.audio_asr.transcribe_pcm
    def fail(*args, **kwargs):
        raise DataAdapterError('audio_incomplete')
    monkeypatch.setattr(audio.audio_asr, 'transcribe_pcm', fail)
    with pytest.raises(DataAdapterError, match='audio_incomplete'):
        transcribe_local_audio(root, identifier, max_seconds=1, allow_network=True)
    monkeypatch.setattr(audio.audio_asr, 'transcribe_pcm', recognize)
    result = transcribe_local_audio(root, identifier, max_seconds=1, allow_network=True, retry_failed=True)
    assert result['completed_seconds'] == 1
    assert result['failure_code'] is None
    saved = json.loads((root / result['transcript_path']).read_text())
    assert len(saved['failed_attempts']) == 1
    assert saved['failed_attempts'][0]['may_have_consumed_quota'] is True
    assert 'in_flight' not in saved
    resumed = transcribe_local_audio(root, identifier, max_seconds=2, allow_network=True)
    assert resumed['whole_audio_transcribed'] and calls == [32000, 64000]


def test_tampering_refuses_paid_call(fixture):
    root, identifier, calls = fixture
    p = prepare_local_audio(root, identifier)
    manifest = root / p['transcript_path']
    data = json.loads(manifest.read_text())
    data['original_file'] = '../../secret'
    manifest.write_text(json.dumps(data))
    with pytest.raises(DataAdapterError, match='audio_cache_invalid'):
        transcribe_local_audio(root, identifier, max_seconds=1, allow_network=True)
    assert not calls
