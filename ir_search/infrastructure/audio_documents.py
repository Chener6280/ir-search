"""Public Xiaoyuzhou episode metadata and explicitly requested ASR windows."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urljoin, urlsplit

from ir_search.registry import DataAdapterError
from ir_search.context import RequestStopped
from .audio_asr import audio_profile, transcribe_pcm
from .credentials import SourceConfigError
from .platform_documents import _document, _read, _text, _platform_url
from .private_files import _directory_lock, _private_read, _private_write
from .public_web import _request, _url

_MEDIA_HOSTS = {'media.xyzcdn.net', 'media.typlog.com'}
_MAX_MEDIA = 96 * 1024 * 1024


@dataclass(frozen=True)
class _Episode:
    url: str
    title: str
    notes: str
    publisher: str
    published: object
    fetched_at: object
    duration: float
    audio_url: str
    media_size: int
    media_state: str


def _episode(url, context):
    platform, url, eid = _platform_url(url)
    if platform != 'xiaoyuzhou': raise DataAdapterError('unsupported')
    reply = _read(url, context=context, hosts={'www.xiaoyuzhoufm.com'})
    try:
        html = reply.body.decode('utf-8')
        script = re.search(r'<script\b[^>]*\bid=["\x27]__NEXT_DATA__["\x27][^>]*>(.*?)</script>', html, re.S)
        if not script: raise ValueError()
        data = json.loads(script[1])['props']['pageProps']['episode']
        if data.get('eid') != eid or data.get('status', 'NORMAL') != 'NORMAL': raise ValueError()
        title, notes = data['title'], data.get('shownotes') or data.get('description') or ''
        if not isinstance(title, str) or not isinstance(notes, str): raise ValueError()
        podcast = data.get('podcast', {})
        publisher = podcast.get('title') or podcast.get('author') or 'unknown'
        if not isinstance(publisher, str): raise ValueError()
        duration = data.get('duration')
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or not 0 < duration <= 604800:
            raise ValueError()
        media = data.get('media') or {}
        source = media.get('source') or {}
        audio = source.get('url') or (data.get('enclosure') or {}).get('url') or ''
        size = media.get('size', 0)
        if not isinstance(audio, str) or type(size) is not int or size < 0: raise ValueError()
        state = 'public' if (not data.get('isPrivateMedia', False) and data.get('payType', 'FREE') == 'FREE'
                            and source.get('mode', 'PUBLIC') == 'PUBLIC' and audio) else 'restricted_or_unavailable'
        published = None
        if data.get('pubDate'):
            published = datetime.fromisoformat(data['pubDate'].replace('Z', '+00:00'))
            if published.utcoffset() is None: raise ValueError()
        return _Episode(url, title[:2000], _text(notes), publisher[:500], published, reply.fetched_at,
                        float(duration), audio if state == 'public' else '', size, state)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise DataAdapterError('upstream_schema') from None


def _media_url(url):
    parsed, host, _ = _url(url)
    if parsed.scheme != 'https' or host not in _MEDIA_HOSTS: raise DataAdapterError('blocked_url')
    suffix = Path(parsed.path).suffix.lower()
    formats = {'.m4a': 'mov', '.mp4': 'mov', '.mp3': 'mp3', '.wav': 'wav', '.flac': 'flac', '.ogg': 'ogg'}
    if suffix not in formats: raise DataAdapterError('audio_format_invalid')
    return formats[suffix]


def _download(url, context):
    for _ in range(4):
        _media_url(url)
        reply = _request(url, context=context, max_bytes=_MAX_MEDIA)
        if reply.status == 200:
            if not reply.body: raise DataAdapterError('audio_format_invalid')
            return reply.body
        target = urljoin(url, reply.location)
        if urlsplit(target).hostname != urlsplit(url).hostname: raise DataAdapterError('blocked_url')
        url = target
    raise DataAdapterError('web_redirect_limit')


def _pcm(path, demuxer, start, seconds, context):
    binary = shutil.which('ffmpeg')
    if not binary: raise DataAdapterError('audio_dependency_missing')
    context.begin_operation()
    with tempfile.TemporaryDirectory(prefix='ir-search-audio-') as directory:
        output = Path(directory) / 'window.pcm'
        command = [binary, '-nostdin', '-v', 'error', '-protocol_whitelist', 'file', '-f', demuxer,
                   '-ss', str(start), '-i', str(path), '-t', str(seconds), '-vn', '-ac', '1', '-ar', '16000',
                   '-c:a', 'pcm_s16le', '-f', 's16le', str(output)]
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            while process.poll() is None:
                context.check_active()
                if output.exists() and output.stat().st_size > seconds * 32000 + 6400:
                    raise DataAdapterError('response_too_large')
                time.sleep(min(.05, context.remaining_seconds()))
            context.check_active()
            if process.returncode or not output.exists(): raise DataAdapterError('audio_format_invalid')
            if not 2 <= output.stat().st_size <= seconds * 32000 + 6400: raise DataAdapterError('audio_format_invalid')
            return output.read_bytes()[:round(seconds * 32000)]
        finally:
            if process.poll() is None: process.kill()
            process.wait()


def _prune(root):
    # Only our hash-named regular cache files; never traverse symlinks.
    paths = [p for p in root.iterdir() if re.fullmatch(r'[a-f0-9]{64}\.(json|bin)', p.name) and not p.is_symlink() and p.is_file()]
    paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    size = 0
    for i, path in enumerate(paths):
        size += path.stat().st_size
        if i >= 128 or size > 256 * 1024 * 1024 or time.time() - path.stat().st_mtime > 30 * 86400:
            path.unlink()


def _validate_cache(record, key, start, seconds, profile):
    """Reject corrupted caches before exposing text or time mappings."""
    try:
        text = record['text']
        end = record['window_end_seconds']
        if (record['cache_id'] != key or not isinstance(text, str) or not 1 <= len(text) <= 100000
                or record['text_hash'] != hashlib.sha256(text.encode()).hexdigest()
                or record.get('provider_final_received') is not True or record.get('machine_transcribed') is not True
                or record.get('model') != profile.model or record.get('resource_id') != profile.resource
                or record.get('window_start_seconds') != start or isinstance(end, bool)
                or not isinstance(end, (int, float)) or not start < end <= start + seconds
                or profile.api_key in text): raise ValueError()
        segments = record['transcript_segments']
        if not isinstance(segments, list) or len(segments) > 3000: raise ValueError()
        previous = 0
        for row in segments:
            a, b, t, u = (row[k] for k in ('start_char', 'end_char', 'start_ms', 'end_ms'))
            if (any(type(v) is not int for v in (a, b, t, u)) or not previous <= a < b <= len(text)
                    or not 0 <= t <= u <= seconds * 1000 + 200): raise ValueError()
            previous = b
    except (ValueError, KeyError, TypeError): raise DataAdapterError('audio_cache_invalid') from None


def _cached_transcription(episode, start, seconds, profile, mode, context):
    demuxer = _media_url(episode.audio_url)
    identity = json.dumps([episode.url, episode.audio_url, start, seconds, profile.model,
                          hashlib.sha256(profile.api_key.encode()).hexdigest(), context.account_scope, 'v1'], separators=(',', ':'))
    key = hashlib.sha256(identity.encode()).hexdigest()
    try:
        with _directory_lock(profile.cache_dir, context) as root:
            _prune(root)
            target = root / (key + '.json')
            if target.exists():
                try:
                    record = json.loads(_private_read(target, 2 * 1024 * 1024))
                    _validate_cache(record, key, start, seconds, profile)
                    return {**record, 'cache_status': 'hit', 'asr_invoked': False}
                except (ValueError, KeyError, TypeError): raise DataAdapterError('audio_cache_invalid') from None
            if mode == 'cache_only': raise DataAdapterError('audio_cache_miss')
            if context.remaining_seconds() < seconds + 15: raise DataAdapterError('audio_time_budget_insufficient')
            if shutil.which('ffmpeg') is None: raise DataAdapterError('audio_dependency_missing')
            try: from websockets.sync.client import connect  # noqa: F401 -- fail before downloading
            except ImportError: raise DataAdapterError('audio_dependency_missing') from None
            media = root / (hashlib.sha256(episode.audio_url.encode()).hexdigest() + '.bin')
            if media.exists() and time.time() - media.stat().st_mtime <= 86400:
                raw = _private_read(media, _MAX_MEDIA)
            else:
                if episode.media_size > _MAX_MEDIA: raise DataAdapterError('audio_media_too_large')
                raw = _download(episode.audio_url, context)
                _private_write(media, raw)
            pcm = _pcm(media, demuxer, start, seconds, context)
            recognized = transcribe_pcm(pcm, profile=profile, context=context)
            record = {**recognized, 'cache_id': key, 'text_hash': hashlib.sha256(recognized['text'].encode()).hexdigest(),
                'audio_sha256': hashlib.sha256(raw).hexdigest(), 'pcm_sha256': hashlib.sha256(pcm).hexdigest(),
                'transcribed_at': datetime.now(timezone.utc).isoformat(), 'window_start_seconds': start,
                'window_end_seconds': start + len(pcm) / 32000}
            _validate_cache(record, key, start, seconds, profile)
            _private_write(target, json.dumps(record, ensure_ascii=False).encode())
            _prune(root)
            return {**record, 'cache_status': 'miss', 'asr_invoked': True}
    except OSError: raise DataAdapterError('audio_cache_unavailable') from None


def _window_batch(episode, start, seconds, count, profile, mode, context):
    """Reuse completed windows and stop at a gap, source change, or bounded failure."""
    records, chunks, segments, manifest = [], [], [], []
    length, failure = 0, None
    for _ in range(count):
        span = min(seconds, episode.duration-start)
        try:
            record = dict(_cached_transcription(episode, start, span, profile, mode, context))
            if records and record['audio_sha256'] != records[0]['audio_sha256']:
                raise DataAdapterError('audio_source_changed')
        except (DataAdapterError, RequestStopped) as exc:
            if not records: raise
            failure = exc.code
            break
        text = record['text']
        offset = length + (2 if chunks else 0)
        chunks.append(text)
        length = offset+len(text)
        segments.extend({**row, 'start_char':row['start_char']+offset,'end_char':row['end_char']+offset,
            'start_ms':row['start_ms']+start*1000,'end_ms':row['end_ms']+start*1000} for row in record['transcript_segments'])
        manifest.append({k:record[k] for k in ('window_start_seconds','window_end_seconds','audio_sha256',
            'pcm_sha256','text_hash','cache_status','asr_invoked')})
        manifest[-1].update(start_char=offset,end_char=length)
        records.append(record)
        end = record['window_end_seconds']
        if end >= episode.duration: break
        # Never join overlapping or missing audio to make a continuous transcript.
        if abs(end-round(end)) >= .01 or end < start+span-.01:
            failure = 'audio_incomplete'
            break
        start = round(end)
    combined = dict(records[0])
    combined.update(text='\n\n'.join(chunks), transcript_segments=segments,
        window_end_seconds=records[-1]['window_end_seconds'],window_manifest=manifest,
        windows_requested=count,windows_completed=len(records),
        cache_status='hit' if all(r['cache_status']=='hit' for r in records) else 'mixed' if any(r['cache_status']=='hit' for r in records) else 'miss',
        asr_invoked=any(r['asr_invoked'] for r in records),
        text_hash=hashlib.sha256('\n\n'.join(chunks).encode()).hexdigest())
    if len(records)>1:
        combined.pop('pcm_sha256',None)
        combined.pop('pcm_duration_ms',None)
    if failure: combined['window_failure_code']=failure
    return combined


def read_audio_document(url, *, context, max_chars=20000, audio_mode='metadata', audio_start_seconds=0, audio_max_seconds=60,
                        audio_window_count=1):
    """Read one public episode; ASR requires explicit transcribe/cache_only mode.

    Windows are bounded to 180 seconds, and may be resumed using the returned
    next_start_seconds. Missing access or ASR errors never become faux transcripts.
    """
    if audio_mode not in {'metadata', 'transcribe', 'cache_only'}: raise ValueError('Invalid audio_mode')
    if (type(audio_start_seconds) is not int or not 0 <= audio_start_seconds <= 604800
            or type(audio_max_seconds) is not int or not 1 <= audio_max_seconds <= 180
            or type(audio_window_count) is not int or not 1 <= audio_window_count <= 5
            or type(max_chars) is not int or not 1 <= max_chars <= 100000): raise ValueError('Invalid audio budget')
    episode = _episode(url, context)
    text = episode.notes[:max_chars]
    details = {'content_origin': 'episode_show_notes', 'episode_duration_seconds': episode.duration,
        'audio_access': episode.media_state, 'asr_invoked': False, 'machine_transcribed': False,
        'transcript_status': 'not_requested', 'audio_mode': audio_mode}
    warnings = ['community_content_not_verified', 'show_notes_not_transcript']
    if len(episode.notes) > max_chars: warnings.append('text_truncated')
    if audio_mode != 'metadata':
        if not episode.audio_url: raise DataAdapterError('audio_access_unavailable')
        if audio_start_seconds >= episode.duration: raise DataAdapterError('audio_window_out_of_range')
        try: profile = audio_profile()
        except SourceConfigError: raise DataAdapterError('source_config_error') from None
        if profile is None: raise DataAdapterError('source_disabled')
        record = _window_batch(episode, audio_start_seconds, audio_max_seconds, audio_window_count, profile, audio_mode, context)
        text = record.pop('text')[:max_chars]
        record.pop('cache_id', None)
        segments = []
        for row in record.pop('transcript_segments'):
            if row['start_char'] >= len(text): break
            segments.append({**row, 'end_char': min(row['end_char'], len(text))})
        end = record['window_end_seconds']
        # Requested windows end at integer seconds except the final tail.
        next_start = round(end) if abs(end - round(end)) < .01 else math.floor(end)
        if end < episode.duration and next_start <= audio_start_seconds: raise DataAdapterError('audio_incomplete')
        details.update(record, content_origin='volcengine_asr', transcript_segments=segments, transcript_status='transcribed',
                       next_start_seconds=next_start if end < episode.duration else None,
                       whole_episode_transcribed=audio_start_seconds == 0 and end >= episode.duration,
                       transcription_scope='requested_window', timestamp_navigation_supported=False)
        warnings = ['community_content_not_verified', 'machine_transcript_requires_audio_verification']
        if not details['whole_episode_transcribed']: warnings.append('audio_partial_episode')
        if record.get('window_failure_code'): warnings.append(record['window_failure_code'])
        if record['text_hash'] != hashlib.sha256(text.encode()).hexdigest(): warnings.append('text_truncated')
    doc = _document(episode.url, 'xiaoyuzhou', episode.title, text, episode.fetched_at,
        published=episode.published, publisher=episode.publisher, details=details, warnings=warnings)
    return replace(doc, content_type='audio', extraction_method='volcengine_agent_plan_asr' if audio_mode != 'metadata' else 'xiaoyuzhou_show_notes')


def _audio_citation(details, text, start, end):
    if details.get('content_origin') != 'volcengine_asr' or not details.get('machine_transcribed'): return {}
    segments = details.get('transcript_segments', [])
    if not isinstance(segments, list) or len(segments) > 3000: return {}
    previous, matched = 0, []
    for row in segments:
        if not isinstance(row, dict): return {}
        a, b, t, u = (row.get(k) for k in ('start_char', 'end_char', 'start_ms', 'end_ms'))
        if any(type(v) is not int for v in (a, b, t, u)) or not (previous <= a < b <= len(text) and 0 <= t <= u <= 604801000): return {}
        previous = b
        if a < end and b > start: matched.append(row)
    if not matched: return {}
    return {'start_ms': min(r['start_ms'] for r in matched), 'end_ms': max(r['end_ms'] for r in matched),
            'time_basis': 'asr_utterance_offsets_not_word_alignment', 'machine_transcribed': True,
            'timestamp_navigation_supported': False}
