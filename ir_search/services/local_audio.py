"""Separate local audio originals and explicit, resumable Volcengine ASR.

Discovery/prepare never call a network. Only transcribe_local_audio with explicit
authorization can send verified PCM to the existing fixed Agent Plan endpoint.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import subprocess
import time
import re
import os
from datetime import datetime, timezone

from ir_search.context import RequestContext, RequestStopped
from ir_search.registry import DataAdapterError
from ir_search.infrastructure import audio_asr
from ir_search.infrastructure.audio_documents import _pcm
from ir_search.infrastructure.private_files import _private_read, _private_write
from .local_archive import (_root, _derived, _database, _writer, _now, _sha, DERIVED,
                            read_local_archive, resolve_local_archive_asset)

AUDIO_FORMATS = {'.mp3': 'mp3', '.m4a': 'mov', '.wav': 'wav', '.flac': 'flac', '.ogg': 'ogg', '.aac': 'aac'}
PCM_LIMIT = 256 * 1024 * 1024


def is_audio_title(title):
    return Path(str(title)).suffix.lower() in AUDIO_FORMATS


def _folder(root, sha):
    return _derived(root, f'{DERIVED}/audio/{sha}', exists=False)


def _load(folder, sha):
    target = folder / 'transcript.json'
    if not target.exists():
        return None
    try:
        r = json.loads(_private_read(target, 32 * 1024 * 1024))
        if (r['schema_version'] != 1 or r['audio_sha256'] != sha or r['sample_rate'] != 16000
                or r['model'] != audio_asr.MODEL or r['resource_id'] != audio_asr.RESOURCE
                or r['text_hash'] != _sha(r['text']) or len(r['text']) > 8_000_000
                or type(r['total_samples']) is not int or not 0 < r['total_samples'] <= PCM_LIMIT//2
                or type(r['next_start_sample']) is not int or not 0 <= r['next_start_sample'] <= r['total_samples']
                or r['whole_audio_transcribed'] != (r['next_start_sample'] == r['total_samples'])):
            raise ValueError()
        if (r['original_file'] not in {'original'+s for s in AUDIO_FORMATS}
                or not re.fullmatch('[0-9a-f]{64}', r['pcm_sha256'])):
            raise ValueError()
        end, chars = 0, 0
        for w in r['windows']:
            if (w['start_sample'] != end or not end < w['end_sample'] <= r['total_samples']
                    or w['start_char'] != chars + (2 if chars else 0)
                    or not w['start_char'] < w['end_char'] <= len(r['text'])
                    or _sha(r['text'][w['start_char']:w['end_char']]) != w['text_hash']):
                raise ValueError()
            end, chars = w['end_sample'], w['end_char']
        if end != r['next_start_sample'] or chars != len(r['text']):
            raise ValueError()
        previous = 0
        for segment in r['transcript_segments']:
            a, b, t, u = (segment[k] for k in ('start_char', 'end_char', 'start_ms', 'end_ms'))
            if (any(type(v) is not int for v in (a, b, t, u)) or not previous <= a < b <= len(r['text'])
                    or not 0 <= t <= u <= round(end/16) + 200):
                raise ValueError()
            previous = b
        return r
    except (ValueError, TypeError, KeyError, OSError):
        raise DataAdapterError('audio_cache_invalid') from None


def _save(folder, r):
    r['updated_at'] = _now()
    _private_write(folder/'transcript.json', json.dumps(r, ensure_ascii=False).encode())


def _report(root, folder, r, *, asr_invoked=False):
    return {'schema_version': 1, 'status': 'ok', 'audio_status': r['status'],
            'document_id': r['document_id'], 'title': r['title'], 'audio_sha256': r['audio_sha256'],
            'duration_seconds': r['total_samples']/16000, 'completed_seconds': r['next_start_sample']/16000,
            'next_start_seconds': None if r['whole_audio_transcribed'] else r['next_start_sample']/16000,
            'whole_audio_transcribed': r['whole_audio_transcribed'], 'windows_completed': len(r['windows']),
            'text_chars': len(r['text']), 'machine_transcribed': bool(r['text']),
            'asr_invoked': asr_invoked, 'network_calls': 1 if asr_invoked else 0,
            'model': r['model'], 'account_billing_mode': 'unknown',
            'asr_started_at': r.get('asr_started_at'), 'asr_finished_at': r.get('asr_finished_at'),
            'successful_asr_elapsed_seconds': round(sum(w.get('elapsed_seconds', 0) for w in r['windows']), 3),
            'failed_asr_elapsed_seconds': round(sum(w.get('elapsed_seconds', 0) for w in r.get('failed_attempts', [])), 3),
            'window_timings': [{k: w.get(k) for k in ('start_sample', 'end_sample', 'started_at', 'transcribed_at', 'elapsed_seconds')} for w in r['windows']],
            'original_path': str((folder/r['original_file']).relative_to(root)),
            'transcript_path': str((folder/'transcript.json').relative_to(root)),
            'text_path': str((folder/'transcript.txt').relative_to(root)),
            'failure_code': r.get('failure_code'), 'retry_automatically': False}


def list_local_audio(archive_root, *, limit=30, offset=0):
    """Read a distinct audio queue; never download or invoke speech recognition."""
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
        raise ValueError('invalid_audio_list_budget')
    root = _root(archive_root)
    with _database(root) as con:
        records = [json.loads(row[0]) for row in con.execute("SELECT payload FROM docs WHERE kind='attachment' ORDER BY id")]
    records = [r for r in records if is_audio_title(r['title'])]
    counts = {'downloaded': 0, 'needs_download': 0, 'prepared': 0, 'transcribed': 0, 'transcribing': 0, 'failed': 0}
    items = []
    activity = []
    # A partial transcript retains status=transcribing between CLI windows.
    # Only a live writer owning an in-flight call is actually transcribing.
    lock = root / DERIVED / '.writer.lock'
    try:
        if lock.is_symlink() or lock.stat().st_size > 32: raise ValueError()
        writer_pid = int(lock.read_text())
        lock_time = lock.stat().st_mtime
        if writer_pid <= 0: raise ValueError()
        os.kill(writer_pid, 0)
    except (OSError, ValueError):
        writer_pid, lock_time = None, None
    for row in records:
        downloaded = row.get('download_status') == 'ok'
        counts['downloaded' if downloaded else 'needs_download'] += 1
        state, progress = ('awaiting_transcription' if downloaded else 'needs_download'), None
        sha = row.get('sha256')
        if downloaded and sha:
            m = _load(_folder(root, sha), sha)
            if m:
                state = m['status']
                counts[state] = counts.get(state, 0) + 1
                progress = {'completed_seconds': m['next_start_sample']/16000,
                            'duration_seconds': m['total_samples']/16000,
                            'whole_audio_transcribed': m['whole_audio_transcribed']}
                flight = m.get('in_flight') or {}
                try:
                    started = datetime.fromisoformat(flight['started_at'].replace('Z', '+00:00')).timestamp()
                    age = datetime.now(timezone.utc).timestamp() - started
                    span = (flight['end_sample'] - flight['start_sample']) / 16000
                    live = (state == 'transcribing' and writer_pid is not None
                            and flight.get('pid') == writer_pid and lock_time <= started + 1
                            and 0 <= age <= span + 120)
                except (KeyError, TypeError, ValueError):
                    live = False
                if live:
                    activity.append({'document_id': row['id'], 'source': row['source'],
                                     'title': row['title'], **progress,
                                     'started_at': flight['started_at'], 'elapsed_seconds': round(age, 1)})
        items.append({k: row.get(k) for k in ('id', 'title', 'source', 'source_ref', 'collection_id', 'published_on', 'download_status', 'download_reason', 'size_bytes')} |
                     {'audio_status': state, 'progress': progress})
    return {'schema_version': 1, 'status': 'ok', 'total': len(records), 'counts': counts,
            'items': items[offset:offset+limit], 'has_more': offset+limit < len(records), 'network_calls': 0,
            'activity': activity,
            'coverage_complete': False, 'download_policy': 'fresh_subscription_scan_and_original_downloader_required'}


def _prepare(root, document_id, context):
    row = read_local_archive(root, document_id, max_chars=1)
    if row['kind'] != 'attachment' or not is_audio_title(row['title']):
        raise ValueError('not_an_audio_attachment')
    source = resolve_local_archive_asset(root, document_id)
    sha = source['sha256']
    folder = _folder(root, sha)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    m = _load(folder, sha)
    if m:
        if _sha(_private_read(folder/m['original_file'], 200*1024*1024)) != sha:
            raise DataAdapterError('audio_source_changed')
        return folder, m
    suffix = Path(row['title']).suffix.lower()
    target = _derived(root, str((folder/('original'+suffix)).relative_to(root)), exists=False)
    raw = Path(source['path']).read_bytes()
    if _sha(raw) != sha:
        raise DataAdapterError('audio_source_changed')
    _private_write(target, raw)
    binary = shutil.which('ffprobe')
    if not binary or not shutil.which('ffmpeg'):
        raise DataAdapterError('audio_dependency_missing')
    context.begin_operation()
    try:
        probe = subprocess.run([binary, '-v', 'error', '-protocol_whitelist', 'file', '-f', AUDIO_FORMATS[suffix],
            '-show_entries', 'format=duration', '-of', 'json', str(target)], capture_output=True,
            timeout=min(20, context.remaining_seconds()), check=True)
        duration = float(json.loads(probe.stdout)['format']['duration'])
    except (subprocess.SubprocessError, ValueError, KeyError):
        raise DataAdapterError('audio_format_invalid') from None
    if not math.isfinite(duration) or not 0 < duration <= 7200:
        raise DataAdapterError('audio_duration_exceeds_budget')
    # Canonicalize once, so chunking follows exact decoded samples, not imprecise
    # compressed-file seeks or container duration estimates. FFmpeg stays offline.
    pcm = _pcm(target, AUDIO_FORMATS[suffix], 0, math.ceil(duration)+1, context)
    if len(pcm) > PCM_LIMIT or len(pcm) % 2:
        raise DataAdapterError('audio_media_too_large')
    _private_write(folder/'pcm16k.s16le', pcm)
    m = {'schema_version': 1, 'status': 'prepared', 'document_id': document_id,
         'title': row['title'], 'source_ref': row['source_ref'], 'audio_sha256': sha,
         'original_file': target.name, 'pcm_sha256': _sha(pcm), 'sample_rate': 16000,
         'total_samples': len(pcm)//2, 'container_duration_seconds': duration,
         'next_start_sample': 0, 'whole_audio_transcribed': False,
         'model': audio_asr.MODEL, 'resource_id': audio_asr.RESOURCE,
         'text': '', 'text_hash': _sha(''), 'transcript_segments': [], 'windows': [],
         'machine_transcribed': True, 'semantic_smoothing': False,
         'warnings': ['machine_transcript_requires_audio_verification'], 'created_at': _now()}
    _save(folder, m)
    return folder, m


def prepare_local_audio(archive_root, document_id, *, context=None):
    """Save a separate verified audio copy and exact PCM; no network or ASR."""
    root = _root(archive_root)
    context = context or RequestContext(timeout_seconds=120, max_operations=10)
    with _writer(root):
        before = time.monotonic()
        folder, m = _prepare(root, document_id, context)
        return {**_report(root, folder, m), 'prepare_elapsed_seconds': round(time.monotonic()-before, 3)}


def transcribe_local_audio(archive_root, document_id, *, max_seconds=60, allow_network=False,
                           retry_failed=False, context=None):
    """Explicitly transcribe at most one 1–180s window; resume from exact samples.

    Persist in-flight intent BEFORE a paid call. Ambiguous/failed calls are never
    automatically retried; retry_failed requires renewed caller authorization.
    """
    if type(max_seconds) is not int or not 1 <= max_seconds <= 180 or type(retry_failed) is not bool:
        raise ValueError('invalid_audio_budget')
    if allow_network is not True:
        raise ValueError('audio_network_not_authorized')
    root = _root(archive_root)
    context = context or RequestContext(timeout_seconds=max_seconds+60, max_operations=20)
    invoked = False
    with _writer(root):
        folder, m = _prepare(root, document_id, context)
        if m['whole_audio_transcribed']:
            _private_write(folder/'transcript.txt', m['text'].encode())
            return _report(root, folder, m)
        if (m.get('in_flight') or m.get('failure_code')) and not retry_failed:
            raise DataAdapterError('audio_retry_requires_confirmation')
        profile = audio_asr.audio_profile()
        if profile is None:
            raise DataAdapterError('source_disabled')
        pcm = _private_read(folder/'pcm16k.s16le', PCM_LIMIT)
        if _sha(pcm) != m['pcm_sha256'] or len(pcm) != 2*m['total_samples']:
            raise DataAdapterError('audio_cache_invalid')
        start = m['next_start_sample']
        end = min(start+max_seconds*16000, m['total_samples'])
        window = pcm[start*2:end*2]
        if context.remaining_seconds() < len(window)/32000+15:
            raise DataAdapterError('audio_time_budget_insufficient')
        try:
            import websockets.sync.client  # fail before recording a paid attempt
        except ImportError:
            raise DataAdapterError('audio_dependency_missing') from None
        started_at = _now()
        m.setdefault('asr_started_at', started_at)
        m['in_flight'] = {'start_sample': start, 'end_sample': end, 'started_at': started_at, 'pid': os.getpid()}
        m['status'] = 'transcribing'
        m.pop('failure_code', None)
        _save(folder, m)
        before = time.monotonic()
        try:
            invoked = True
            result = audio_asr.transcribe_pcm(window, profile=profile, context=context)
            if not result.get('provider_final_received') or not result.get('machine_transcribed') or not result.get('text'):
                raise DataAdapterError('audio_incomplete')
            text = result['text']
            offset = len(m['text']) + (2 if m['text'] else 0)
            m['text'] += ('\n\n' if m['text'] else '') + text
            if len(m['text']) > 8_000_000:
                raise DataAdapterError('response_too_large')
            m['transcript_segments'].extend({**s, 'start_char': s['start_char']+offset,
                'end_char': s['end_char']+offset, 'start_ms': s['start_ms']+round(start/16),
                'end_ms': s['end_ms']+round(start/16)} for s in result['transcript_segments'])
            m['windows'].append({'start_sample': start, 'end_sample': end, 'start_char': offset,
                                'end_char': len(m['text']), 'text_hash': _sha(text), 'pcm_sha256': _sha(window),
                                'started_at': started_at, 'transcribed_at': _now(),
                                'elapsed_seconds': round(time.monotonic()-before, 3), 'provider_final_received': True})
            m.update(next_start_sample=end, whole_audio_transcribed=end == m['total_samples'],
                     status='transcribed' if end == m['total_samples'] else 'transcribing', text_hash=_sha(m['text']))
            m.pop('in_flight', None)
            if m['whole_audio_transcribed']:
                m['asr_finished_at'] = _now()
            _save(folder, m)
            _private_write(folder/'transcript.txt', m['text'].encode())
        except Exception as exc:
            # Error messages may contain provider information: keep only known codes.
            m = _load(folder, m['audio_sha256'])
            m.update(status='failed', failure_code=exc.code if isinstance(exc, (DataAdapterError, RequestStopped)) else 'audio_local_failure')
            m.setdefault('failed_attempts', []).append({'started_at': started_at, 'finished_at': _now(),
                'elapsed_seconds': round(time.monotonic()-before, 3), 'code': m['failure_code'],
                'start_sample': start, 'end_sample': end, 'may_have_consumed_quota': True})
            metrics = getattr(exc, 'response_metrics', {})
            if metrics:
                m['failed_attempts'][-1]['response_metrics'] = {k: v for k, v in metrics.items()
                    if k in {'frames_received', 'response_bytes_received', 'frame_limit', 'response_byte_limit',
                             'pcm_bytes_sent', 'pcm_bytes_total'} and type(v) is int}
            _save(folder, m)
            raise
        report = _report(root, folder, m, asr_invoked=invoked)
    from .local_archive import index_local_archive
    index_local_archive(root)
    return report


def indexed_audio(root, sha):
    """Read verified local transcript state for indexing; no ASR or credentials."""
    m = _load(_folder(root, sha), sha)
    if not m:
        return {'parse_status': 'audio_pending', 'text': '', 'text_scope': 'metadata'}
    return {'parse_status': 'audio_transcribed' if m['whole_audio_transcribed'] else 'audio_failed' if m['status']=='failed' else 'audio_partial' if m['text'] else 'audio_pending',
            'text': m['text'], 'text_scope': 'extracted_text' if m['text'] else 'metadata',
            'machine_transcribed': bool(m['text']), 'content_origin': 'volcengine_asr',
            'parser': 'volcengine_agent_plan_asr', 'model': m['model'],
            'transcript_segments': m['transcript_segments'], 'whole_audio_transcribed': m['whole_audio_transcribed'],
            'completed_seconds': m['next_start_sample']/16000, 'duration_seconds': m['total_samples']/16000}
