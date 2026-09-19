"""Explicit, bounded speech recognition using the Volcengine Agent Plan only.

This is an extraction service, never invoked by deterministic material discovery.
No URL transcription API, ordinary-paygo fallback, retries, or summary generation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import gzip
import importlib.util
import json
import logging
from pathlib import Path
import shutil
import socket
import ssl
import struct
import threading
import time
import uuid
import zlib

from ir_search.context import RequestStopped
from ir_search.registry import DataAdapterError
from .credentials import SourceConfigError, credentials_path, read_credentials
from .public_web import _resolve

ENDPOINT = 'wss://openspeech.bytedance.com/api/v3/plan/sauc/bigmodel_nostream'
RESOURCE = 'volc.seedasr.sauc.duration'
MODEL = 'doubao-seed-asr-2.0'
_MAX_FRAME = 1024 * 1024


@dataclass(frozen=True)
class AudioProfile:
    api_key: str = field(repr=False)
    cache_dir: Path = field(repr=False)
    model: str = MODEL
    endpoint: str = ENDPOINT
    resource: str = RESOURCE

    def __post_init__(self):
        if (self.model != MODEL or self.endpoint != ENDPOINT or self.resource != RESOURCE
                or not isinstance(self.api_key, str) or not 1 <= len(self.api_key) <= 4096
                or any(ord(c) < 33 or ord(c) > 126 for c in self.api_key)):
            raise SourceConfigError()


def audio_profile(*, values=None, env_file=None):
    """Load the opt-in Agent Plan profile without contacting the provider."""
    values = read_credentials(env_file) if values is None else values
    enabled = values.get('VOLC_ASR_ENABLED', 'false').lower()
    if enabled not in {'true', 'false'}: raise SourceConfigError()
    if enabled == 'false': return None
    if values.get('VOLC_ASR_PLAN', 'agent_plan') != 'agent_plan': raise SourceConfigError()
    key = values.get('VOLC_ASR_API_KEY', '')
    if not key: raise SourceConfigError('source_credentials_missing')
    root = Path(values.get('AUDIO_CACHE_DIR', '.local/audio-cache')).expanduser()
    if not root.is_absolute(): root = credentials_path(env_file).absolute().parent / root
    return AudioProfile(key, root, values.get('VOLC_ASR_MODEL', MODEL),
                        values.get('VOLC_ASR_ENDPOINT', ENDPOINT), values.get('VOLC_ASR_RESOURCE_ID', RESOURCE))


def audio_configuration_status(*, values=None, env_file=None):
    """Expose readiness only; neither keys nor account entitlement are returned."""
    try:
        profile = audio_profile(values=values, env_file=env_file)
        return {'enabled': profile is not None, 'configured': profile is not None,
                'plan': 'agent_plan', 'model': MODEL, 'resource_id': RESOURCE,
                'websockets_installed': importlib.util.find_spec('websockets') is not None,
                'ffmpeg_installed': shutil.which('ffmpeg') is not None,
                'live_verified': False, 'account_billing_mode': 'unknown', 'automatic_transcription': False}
    except SourceConfigError as exc:
        return {'configured': False, 'code': exc.code, 'live_verified': False}


def _packet(payload, sequence, *, audio=False, last=False):
    payload = gzip.compress(payload)
    flags = 3 if last else 1
    return bytes((0x11, (0x20 if audio else 0x10) | flags, 0x01 if audio else 0x11, 0)) + struct.pack(
        '>iI', -abs(sequence) if last else sequence, len(payload)) + payload


def _decode(frame):
    if not isinstance(frame, bytes) or not 8 <= len(frame) <= _MAX_FRAME: raise DataAdapterError('upstream_schema')
    version, size = frame[0] >> 4, (frame[0] & 15) * 4
    kind, flags, serialization, compression = frame[1] >> 4, frame[1] & 15, frame[2] >> 4, frame[2] & 15
    if version != 1 or size != 4 or kind not in {9, 15} or flags not in {0, 1, 2, 3} or serialization != 1 or compression not in {0, 1}:
        raise DataAdapterError('upstream_schema')
    offset = size
    if flags & 1: offset += 4
    code = None
    if kind == 15:
        if len(frame) < offset + 4: raise DataAdapterError('upstream_schema')
        code = struct.unpack_from('>I', frame, offset)[0]; offset += 4
    if len(frame) < offset + 4: raise DataAdapterError('upstream_schema')
    length = struct.unpack_from('>I', frame, offset)[0]; offset += 4
    if length != len(frame) - offset: raise DataAdapterError('upstream_schema')
    raw = frame[offset:]
    try:
        if compression:
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
            raw = decoder.decompress(raw, _MAX_FRAME + 1)
            if len(raw) > _MAX_FRAME or not decoder.eof or decoder.unused_data: raise ValueError()
        payload = json.loads(raw)
        if not isinstance(payload, dict): raise ValueError()
    except (ValueError, zlib.error, UnicodeError, RecursionError):
        raise DataAdapterError('upstream_schema') from None
    code = code if code is not None else payload.get('code', 20000000)
    if code != 20000000:
        raise DataAdapterError('audio_format_invalid' if code == 45000151 else
                               'rate_limit' if code == 55000031 else 'audio_provider_error')
    return bool(flags & 2), payload


def _stream(pcm, profile, context):
    try:
        from websockets.sync.client import connect
        from websockets.exceptions import InvalidStatus
    except ImportError: raise DataAdapterError('audio_dependency_missing') from None
    context.begin_operation()
    host = 'openspeech.bytedance.com'
    family, kind, protocol, _, sockaddr = _resolve(host, 443, context)
    raw = socket.socket(family, kind, protocol)
    ws, watcher = None, None
    done = threading.Event()
    # A private logger also suppresses third-party debug header/frame logging.
    logger = logging.Logger('ir_search.audio.private', level=logging.CRITICAL + 1)
    logger.addHandler(logging.NullHandler()); logger.propagate = False
    try:
        raw.settimeout(min(10, context.remaining_seconds()))
        raw.connect(sockaddr)
        ws = connect(profile.endpoint, sock=raw, ssl=ssl.create_default_context(), server_hostname=host,
            proxy=None, compression=None, additional_headers={
                'X-Api-Key': profile.api_key, 'X-Api-Resource-Id': profile.resource,
                'X-Api-Connect-Id': str(uuid.uuid4()), 'X-Api-Request-Id': str(uuid.uuid4())},
            open_timeout=min(10, context.remaining_seconds()), close_timeout=.2,
            max_size=_MAX_FRAME, max_queue=4, logger=logger)

        def watch():
            while not done.wait(.05):
                try: context.check_active()
                except RequestStopped:
                    try: ws.socket.shutdown(socket.SHUT_RDWR)
                    except OSError: pass
                    return
        watcher = threading.Thread(target=watch, daemon=True); watcher.start()
        config = {'user': {'uid': 'ir-search'},
            'audio': {'format': 'pcm', 'codec': 'raw', 'rate': 16000, 'bits': 16, 'channel': 1},
            'request': {'model_name': 'bigmodel', 'enable_itn': False, 'enable_punc': True,
                        'enable_ddc': False, 'show_utterances': True, 'result_type': 'full'}}
        ws.send(_packet(json.dumps(config).encode(), 1))
        position, seq, count, received_bytes, next_send = 0, 2, 0, 0, time.monotonic()
        while True:
            context.check_active()
            if position < len(pcm) and time.monotonic() >= next_send:
                chunk = pcm[position:position + 6400]
                position += len(chunk)
                ws.send(_packet(chunk, seq, audio=True, last=position == len(pcm)))
                seq += 1
                next_send = time.monotonic() + .2
            wait = max(.001, min(.2, next_send - time.monotonic())) if position < len(pcm) else .2
            try: frame = ws.recv(timeout=min(wait, context.remaining_seconds()))
            except TimeoutError: continue
            count += 1
            received_bytes += len(frame)
            if count > 4096 or received_bytes > 16 * _MAX_FRAME: raise DataAdapterError('response_too_large')
            final, payload = _decode(frame)
            if final:
                if position < len(pcm): raise DataAdapterError('audio_incomplete')
                context.check_active()
                return payload
    except (DataAdapterError, RequestStopped): raise
    except InvalidStatus as exc:
        code = exc.response.status_code
        raise DataAdapterError('authentication_failed' if code == 401 else 'entitlement_denied' if code == 403 else
                               'quota' if code == 429 else 'audio_provider_error') from None
    except ssl.SSLError: raise DataAdapterError('tls_error') from None
    except Exception:
        context.check_active()
        raise DataAdapterError('audio_incomplete') from None
    finally:
        done.set()
        if ws is not None:
            try: ws.close()
            except Exception: pass
        raw.close()
        if watcher is not None: watcher.join(.3)


def transcribe_pcm(pcm, *, profile, context):
    """Recognize one 16 kHz, signed 16-bit mono PCM window of at most 180 s.

    Only a terminal provider result is accepted. A failed stream is never retried.
    The caller must explicitly budget enough wall time for real-time streaming.
    """
    if not isinstance(profile, AudioProfile): raise ValueError('AudioProfile required')
    if not isinstance(pcm, bytes) or not 2 <= len(pcm) <= 180 * 32000 or len(pcm) % 2:
        raise ValueError('PCM must contain 0..180 seconds of 16 kHz mono 16-bit audio')
    context.check_active()
    if context.remaining_seconds() < len(pcm) / 32000 + 10:
        raise DataAdapterError('audio_time_budget_insufficient')
    payload = _stream(pcm, profile, context)
    result = payload.get('result', {})
    if not isinstance(result, dict): raise DataAdapterError('upstream_schema')
    rows = result.get('utterances', [])
    if not isinstance(rows, list) or len(rows) > 3000: raise DataAdapterError('upstream_schema')
    parts, segments, length = [], [], 0
    limit_ms = round(len(pcm) / 32)
    previous_time = 0
    for row in rows:
        if not isinstance(row, dict): raise DataAdapterError('upstream_schema')
        text, start, end = row.get('text'), row.get('start_time'), row.get('end_time')
        if (not isinstance(text, str) or len(text) > 50000 or any(type(v) is not int for v in (start, end))
                or not 0 <= start <= end <= limit_ms + 200 or start < previous_time):
            raise DataAdapterError('upstream_schema')
        previous_time = start
        if not text.strip(): continue
        if parts: parts.append('\n'); length += 1
        segments.append({'start_char': length, 'end_char': length + len(text), 'start_ms': start, 'end_ms': end})
        parts.append(text); length += len(text)
        if length > 100000: raise DataAdapterError('response_too_large')
    if not parts:
        text = result.get('text', '')
        if not isinstance(text, str) or len(text) > 100000: raise DataAdapterError('upstream_schema')
        if not text.strip(): raise DataAdapterError('audio_no_speech')
        parts = [text]  # Plain text is usable, but absent timings are never invented.
    text = ''.join(parts)
    if profile.api_key in text: raise DataAdapterError('upstream_schema')
    return {'text': text, 'transcript_segments': segments, 'model': profile.model,
            'resource_id': profile.resource, 'pcm_duration_ms': limit_ms,
            'machine_transcribed': True, 'semantic_smoothing': False, 'inverse_text_normalization': False,
            'provider_final_received': True}
