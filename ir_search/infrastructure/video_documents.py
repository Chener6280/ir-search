"""Video metadata and available platform captions, with time-addressable evidence.

No media download, speech recognition, translation, login or generated summary.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import re
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from ir_search.contracts.materials import _video_languages
from ir_search.context import RequestStopped
from ir_search.registry import DataAdapterError
from .public_web import _request, _url
from .platform_documents import _platform_url, _read, _json_read, _text, _document, _epoch, _cookie
from .public_dns import _youtube_dns_mode

_LANGUAGES = ('zh-Hans', 'zh-CN', 'zh', 'en')


def _transcript(rows, max_chars):
    if not isinstance(rows, list) or len(rows) > 20000: raise DataAdapterError('upstream_schema')
    parts, segments, length, truncated = [], [], 0, False
    for row in rows:
        if not isinstance(row, dict): raise DataAdapterError('upstream_schema')
        start, duration, text = row.get('start'), row.get('duration'), row.get('text')
        if (not isinstance(text, str) or any(isinstance(v, bool) or not isinstance(v, (int, float))
                or not math.isfinite(v) or v < 0 or v > 604800 for v in (start, duration))):
            raise DataAdapterError('upstream_schema')
        text = _text(text).strip()
        if not text: continue
        separator = '\n' if parts else ''
        available = max_chars - length - len(separator)
        if available <= 0:
            truncated = True
            break
        clipped = text[:available]
        offset = length + len(separator)
        parts.append(separator + clipped)
        length = offset + len(clipped)
        segments.append({'start_ms': round(start * 1000), 'end_ms': round((start + duration) * 1000),
                         'start_char': offset, 'end_char': length})
        if len(clipped) < len(text):
            truncated = True
            break
    return ''.join(parts), segments, truncated


def _time_citation(details, url, text, start, end):
    """Never attach times to metadata/snippets or to malformed third-party mappings."""
    if details.get('content_origin') != 'platform_captions': return {}
    try: platform, canonical, _ = _platform_url(url)
    except (DataAdapterError, ValueError): return {}
    if platform not in {'youtube', 'bilibili'}: return {}
    segments = details.get('transcript_segments', [])
    if not isinstance(segments, list) or len(segments) > 20000: return {}
    previous, matched = 0, []
    for row in segments:
        if not isinstance(row, dict): return {}
        a, b, t, u = (row.get(k) for k in ('start_char', 'end_char', 'start_ms', 'end_ms'))
        if any(type(v) is not int for v in (a, b, t, u)) or not (previous <= a < b <= len(text) and 0 <= t <= u <= 1209600000): return {}
        previous = b
        if a < end and b > start: matched.append(row)
    if not matched: return {}
    t, u = min(r['start_ms'] for r in matched), max(r['end_ms'] for r in matched)
    return {'start_ms': t, 'end_ms': u, 'timestamp_url': canonical + ('&' if '?' in canonical else '?') + 't=' + str(t // 1000),
            'time_basis': 'overlapping_caption_segments_not_word_alignment',
            'caption_kind': details.get('caption_kind', 'unknown'), 'caption_language': details.get('caption_language', 'unknown')}


class _NoConsent:
    def set(self, *args, **kwargs):
        raise DataAdapterError('web_content_challenge')


class _YouTubeSession:
    """Duck-typed requests session: fixed read endpoints through DNS-pinned transport."""
    def __init__(self, context, video_id):
        self.context, self.video_id = context, video_id
        self.dns_mode = _youtube_dns_mode()
        self.headers, self.cookies = {}, _NoConsent()

    def _response(self, reply):
        from requests import Response
        if reply.status != 200: raise DataAdapterError('web_content_challenge')
        response = Response()
        response.status_code, response._content, response.encoding = 200, reply.body, 'utf-8'
        return response

    def get(self, url):
        parsed, host, _ = _url(url, signed_download=True)
        query = parse_qs(parsed.query)
        if parsed.scheme != 'https' or host != 'www.youtube.com': raise DataAdapterError('blocked_url')
        if parsed.path == '/watch' and query.get('v') == [self.video_id]:
            signed = False
        elif parsed.path == '/api/timedtext' and query.get('v') == [self.video_id]:
            signed = True
        else: raise DataAdapterError('blocked_url')
        return self._response(_request(url, context=self.context, max_bytes=4 * 1024 * 1024, signed_download=signed,
                                       **({'dns_mode':self.dns_mode} if self.dns_mode != 'system' else {})))

    def post(self, url, *, json):
        # The embedded public application key goes only to the fixed read-only
        # player endpoint, in a header. It is never persisted or exposed to callers.
        parsed, host, _ = _url(url, signed_download=True)
        query = parse_qs(parsed.query)
        if (parsed.scheme != 'https' or host != 'www.youtube.com' or parsed.path != '/youtubei/v1/player'
                or set(query) != {'key'} or len(query['key']) != 1
                or not re.fullmatch(r'[A-Za-z0-9_-]{8,200}', query['key'][0])
                or not isinstance(json, dict) or json.get('videoId') != self.video_id
                or set(json) != {'context', 'videoId'}):
            raise DataAdapterError('blocked_url')
        import json as encoder
        body = encoder.dumps(json).encode()
        if len(body) > 65536: raise DataAdapterError('upstream_schema')
        target = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', ''))
        return self._response(_request(target, context=self.context, method='POST', body=body,
            headers={'Content-Type': 'application/json', 'X-Goog-Api-Key': query['key'][0]}, max_bytes=4 * 1024 * 1024,
            **({'dns_mode':self.dns_mode} if self.dns_mode != 'system' else {})))


def _youtube_captions(identifier, languages, context):
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError: raise DataAdapterError('video_dependency_missing') from None
    try:
        transcript = YouTubeTranscriptApi(http_client=_YouTubeSession(context, identifier)).fetch(identifier, languages=languages)
        return transcript.to_raw_data(), transcript.language_code, 'automatic' if transcript.is_generated else 'manual'
    except (DataAdapterError, RequestStopped): raise
    except Exception as exc:
        code = {'TranscriptsDisabled': 'video_captions_unavailable', 'NoTranscriptFound': 'video_caption_language_unavailable',
                'VideoUnavailable': 'not_found', 'RequestBlocked': 'web_content_challenge', 'IpBlocked': 'web_content_challenge',
                'AgeRestricted': 'authentication_failed', 'VideoUnplayable': 'entitlement_denied'}.get(type(exc).__name__, 'upstream_schema')
        raise DataAdapterError(code) from None


def _bili_data(url, context, cookie=''):
    data, reply = _json_read(url, context=context, hosts={'api.bilibili.com'}, cookie=cookie)
    code = data.get('code')
    if type(code) is not int:
        raise DataAdapterError('upstream_schema')
    if code != 0:
        raise DataAdapterError({-101: 'authentication_failed', -102: 'authentication_failed',
            -404: 'not_found', 62002: 'not_found', -403: 'entitlement_denied',
            -352: 'web_content_challenge', -412: 'web_content_challenge',
            412: 'web_content_challenge', -509: 'rate_limit'}.get(code, 'upstream_schema'))
    if not isinstance(data.get('data'), dict): raise DataAdapterError('upstream_schema')
    return data['data'], reply


def read_video_document(url, *, context, max_chars=20000, languages=_LANGUAGES):
    """Read metadata and available captions; missing captions return uncitable metadata."""
    if type(max_chars) is not int or not 1 <= max_chars <= 100000: raise ValueError('Invalid text limit')
    languages = _video_languages(languages)
    platform, url, identifier = _platform_url(url)
    if platform not in {'youtube', 'bilibili'}: raise DataAdapterError('unsupported')
    title, publisher, description, published = '', '', '', None
    fetched_at, warnings, text = datetime.now(timezone.utc), ['video_content_not_verified'], ''
    details = {'content_origin': 'video_metadata', 'caption_status': 'not_read', 'description_is_transcript': False,
               'transcript_segments': [], 'media_downloaded': False, 'speech_recognition_used': False}
    if platform == 'bilibili':
        cookie = _cookie('BILIBILI_COOKIE')
        old_id = identifier.startswith('av')
        data, reply = _bili_data('https://api.bilibili.com/x/web-interface/view?' + ('aid=' + identifier[2:] if old_id else 'bvid=' + identifier), context, cookie)
        if (data.get('aid') != int(identifier[2:]) if old_id else data.get('bvid') != identifier):
            raise DataAdapterError('upstream_schema')
        bvid = data.get('bvid')
        if not isinstance(bvid, str) or not re.fullmatch(r'BV[A-Za-z0-9]{10}', bvid): raise DataAdapterError('upstream_schema')
        owner = data.get('owner') or {}
        if not isinstance(owner, dict): raise DataAdapterError('upstream_schema')
        title, publisher, description = data.get('title', ''), owner.get('name', ''), data.get('desc', '')
        aid = data.get('aid')
        published, fetched_at = _epoch(data.get('pubdate')), reply.fetched_at
        part = int(parse_qs(urlsplit(url).query).get('p', ['1'])[0])
        pages = data.get('pages', [])
        if not isinstance(pages, list) or len(pages) > 1000: raise DataAdapterError('upstream_schema')
        page = next((p for p in pages if isinstance(p, dict) and p.get('page') == part), None)
        if part != 1 and page is None: raise DataAdapterError('not_found')
        cid = page.get('cid') if page else data.get('cid') if part == 1 else None
        duration = page.get('duration') if page else data.get('duration')
        if type(duration) not in {int, float} or not 0 <= duration <= 604800:
            duration = None
        details.update(video_id=identifier, part=part, duration_seconds=duration,
                       caption_completeness='not_verified')
    else:
        dns_mode = _youtube_dns_mode()
        details['network_route'] = 'google_doh_direct_tls' if dns_mode == 'google_doh' else 'system_dns_direct_tls'
        try:
            data, reply = _json_read('https://www.youtube.com/oembed?' + urlencode({'url': url, 'format': 'json'}),
                                     context=context, hosts={'www.youtube.com'},
                                     **({'dns_mode':dns_mode} if dns_mode != 'system' else {}))
            title, publisher, fetched_at = data.get('title', ''), data.get('author_name', ''), reply.fetched_at
        except DataAdapterError as exc:
            warnings.append('video_metadata_' + exc.code)
        details['video_id'] = identifier
    if any(not isinstance(v, str) for v in (title, publisher, description)): raise DataAdapterError('upstream_schema')
    details.update(description=description[:10000], title=title[:2000], author=publisher[:500])
    try:
        if platform == 'youtube':
            rows, language, kind = _youtube_captions(identifier, languages, context)
        else:
            if type(cid) is not int or cid <= 0: raise DataAdapterError('upstream_schema')
            data, _ = _bili_data('https://api.bilibili.com/x/player/v2?' + urlencode({'bvid': bvid, 'cid': cid}), context, cookie)
            # Caption metadata must describe the requested video/part, not a
            # different object returned by a stale or malformed upstream reply.
            for key, expected in (('bvid', bvid), ('cid', cid), ('aid', aid)):
                if key in data and expected is not None and data[key] != expected:
                    raise DataAdapterError('upstream_schema')
            subtitle = data.get('subtitle') or {}
            if not isinstance(subtitle, dict): raise DataAdapterError('upstream_schema')
            tracks = subtitle.get('subtitles', [])
            if not isinstance(tracks, list) or len(tracks) > 100: raise DataAdapterError('upstream_schema')
            if data.get('need_login_subtitle') is True:
                raise DataAdapterError('video_captions_login_required')
            if not tracks: raise DataAdapterError('video_captions_unavailable')
            if any(not isinstance(t, dict) or not isinstance(t.get('lan'), str)
                   or not isinstance(t.get('subtitle_url', ''), str) for t in tracks):
                raise DataAdapterError('upstream_schema')
            matching = [t for lang in languages for t in tracks if t['lan'].removeprefix('ai-') == lang]
            if not matching: raise DataAdapterError('video_caption_language_unavailable')
            # The platform sometimes returns a named track with no download URL.
            # Skip only these empty entries; do not relax URL or redirect checks.
            track = next((t for t in matching if t.get('subtitle_url')), None)
            if not track: raise DataAdapterError('video_caption_url_unavailable')
            if any(not t.get('subtitle_url') for t in matching): warnings.append('video_caption_track_url_missing')
            link = track['subtitle_url']
            if link.startswith('//'): link = 'https:' + link
            _, host, _ = _url(link, signed_download=True)
            if host not in {'aisubtitle.hdslb.com', 'i0.hdslb.com', 'i1.hdslb.com', 'i2.hdslb.com'}:
                raise DataAdapterError('blocked_url')
            data, _ = _json_read(link, context=context, hosts={host}, signed=True)
            body = data.get('body')
            if not isinstance(body, list) or len(body) > 20000: raise DataAdapterError('upstream_schema')
            rows = []
            for item in body:
                if (not isinstance(item, dict) or any(type(item.get(k)) not in {int, float}
                        or not math.isfinite(item[k]) for k in ('from', 'to'))
                        or not 0 <= item['from'] <= 604800
                        or not 0 <= item['to'] - item['from'] <= 604800):
                    raise DataAdapterError('upstream_schema')
                rows.append({'start': item['from'], 'duration': item['to'] - item['from'], 'text': item.get('content')})
            last_end = max((row['start'] + row['duration'] for row in rows), default=0)
            details.update(caption_rows_returned=len(rows), caption_returned_end_seconds=last_end)
            if duration and last_end > duration + max(5, duration * 0.01):
                raise DataAdapterError('video_caption_timing_mismatch')
            language, kind = track.get('lan', ''), 'automatic' if track.get('ai_type') or track.get('lan', '').startswith('ai-') else 'unknown'
        text, segments, truncated = _transcript(rows, max_chars)
        if not text: raise DataAdapterError('video_captions_unavailable')
        details.update(content_origin='platform_captions', caption_status='available', caption_language=language,
                       caption_kind=kind, transcript_segments=segments, all_returned_caption_rows_included=not truncated)
        if platform == 'bilibili':
            if duration and last_end < duration - max(15, duration * 0.1):
                # This is a time-range observation, not proof of missing speech:
                # music, silence and credits can also extend beyond subtitles.
                warnings.append('video_caption_ends_before_video')
        if truncated: warnings.append('text_truncated')
        if kind == 'automatic': warnings.append('automatic_captions_may_have_errors')
    except DataAdapterError as exc:
        details.update(caption_status=exc.code, all_returned_caption_rows_included=False)
        warnings.extend([exc.code, 'video_metadata_only'])
    if not title.strip() and not text:
        # The caller's URL alone is not successful retrieval of platform metadata.
        raise DataAdapterError(details.get('caption_status', 'no_extracted_text'))
    context.check_active()
    return _document(url, platform, title, text, fetched_at, published=published, publisher=publisher,
                     details=details, warnings=warnings)
