"""Source-scoped summaries and partial machine transcripts, never asserted full text."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import unescape
import json
import re

from .alphapai import AlphapaiClient, alphapai_profile, _identifier, _parse_reference, _reference
from .credentials import SourceConfigError
from ir_search.documents.html import extract_html_document
from ir_search.documents.models import Document, hash_text, make_doc_id
from ir_search.models import EvidenceType, SourceTier
from ir_search.registry import DataAdapterError

CST = timezone(timedelta(hours=8))


def _plain(value, limit=100000):
    if value is None: return ''
    if not isinstance(value, str): raise DataAdapterError('upstream_schema')
    if '<' in value and '>' in value:
        return extract_html_document(value.encode(), 'https://alphapai-web.rabyte.cn/', max_chars=limit).text.strip()
    return unescape(value).strip()[:limit]


def _time(value):
    if value is None or value == '': return None
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d\d-\d\d \d\d:\d\d:\d\d', value):
        raise DataAdapterError('upstream_schema')
    try: return datetime.strptime(value, '%Y-%m-%d %H:%M:%S').replace(tzinfo=CST)
    except ValueError: raise DataAdapterError('upstream_schema') from None


def _metadata(row):
    try: identifier = _identifier(row.get('id'))
    except DataAdapterError: raise DataAdapterError('upstream_schema') from None
    def label(value, limit):
        if value is None: return ''
        if not isinstance(value, str): raise DataAdapterError('upstream_schema')
        return re.sub(r'\s+', ' ', unescape(re.sub(r'<[^>]+>', '', value))).strip()[:limit]
    title = label(row.get('title'), 2000)
    if not title: raise DataAdapterError('upstream_schema')
    publisher = label(row.get('publishInstitution'), 200) or 'unknown'
    published, meeting = _time(row.get('date')), _time(row.get('roadshowDate'))
    symbols = row.get('stock') or []
    if not isinstance(symbols, list) or len(symbols) > 100: raise DataAdapterError('upstream_schema')
    codes = tuple(dict.fromkeys(s['code'] for s in symbols if isinstance(s, dict)
        and isinstance(s.get('code'), str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.\-]{0,29}', s['code'])))
    return identifier, title, publisher, published, meeting, codes


def _transcript(value, limit):
    """Parse vendor JSON segments without asserting timestamp units or completeness."""
    if not isinstance(value, str) or not value.strip(): raise DataAdapterError('no_extracted_text')
    if not value.lstrip().startswith('['):
        text = _plain(value, limit+1)
        return text[:limit], [], len(text)>limit, 'provider_unstructured_transcript'
    try: rows = json.loads(value)
    except (ValueError, TypeError, RecursionError): raise DataAdapterError('upstream_schema') from None
    if not isinstance(rows, list) or len(rows) > 20000: raise DataAdapterError('upstream_schema')
    pieces, segments, size, clipped, previous = [], [], 0, False, -1
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('content'), str): raise DataAdapterError('upstream_schema')
        start, end = row.get('bg'), row.get('ed')
        if (type(start) is not int or type(end) is not int or not 0 <= start <= end <= 10**10
                or start < previous): raise DataAdapterError('upstream_schema')
        previous = start
        content = row['content'].strip()
        if not content: continue
        if size >= limit: clipped = True; break
        separator = '\n' if pieces else ''
        remaining = limit-size-len(separator)
        if remaining <= 0: clipped = True; break
        part = content[:remaining]
        offset = size+len(separator)
        pieces.append(separator+part); size = offset+len(part)
        role = row.get('role')
        # Role is an anonymous vendor speaker identifier, not an inferred identity.
        role = str(role) if isinstance(role, (str, int)) and len(str(role)) <= 40 else 'unknown'
        segments.append({'start_char':offset, 'end_char':size, 'speaker_role':role,
                         'source_start':start, 'source_end':end, 'source_time_unit':'unverified'})
        if part != content: clipped = True; break
    text = ''.join(pieces)
    if not text: raise DataAdapterError('no_extracted_text')
    return text, segments, clipped, 'provider_machine_transcript'


def _document(reference, reply, max_chars):
    identifier, kind = _parse_reference(reference)
    row = reply.data
    rid, title, publisher, published, meeting, symbols = _metadata(row)
    if row.get('_requested_id', rid) != identifier: raise DataAdapterError('upstream_schema')
    warnings = ['source_uri_not_web_url', 'alphapai_reference_stability_unverified',
        'alphapai_source_claims_not_independently_verified', 'alphapai_time_assumed_asia_shanghai',
        'alphapai_permission_flags_have_different_scopes', 'not_full_text']
    if reply.cache_state == 'write_failed': warnings.append('alphapai_cache_write_failed')
    details = {'text_provider':'alphapai', 'source_text_trust':'untrusted', 'access_method':'account_browser',
        'collection':'shared_meetings', 'complete':False, 'cache_state':reply.cache_state,
        'reference_stability':'unverified_provider_identifier', 'publication_basis':'provider_date_assumed_cst',
        'publisher_identity_verification':'provider_label_only',
        'provider_identifier_rotated':rid != identifier,
        'meeting_at':meeting.isoformat() if meeting else None, 'symbols':list(symbols),
        'permissions':{k:row.get(k) if type(row.get(k)) is bool else None for k in ('hasPermission','freeAccess')},
        'available_text_references':{}}
    if isinstance(row.get('sharePermission'), dict):
        value = row['sharePermission'].get('hasPermission')
        details['permissions']['share_has_permission'] = value if type(value) is bool else None
    for name, key in (('summary','aiSummary'), ('transcript','mtSummary')):
        if isinstance(row.get(key), dict) and isinstance(row[key].get('content'), str) and row[key]['content'].strip():
            details['available_text_references'][name] = _reference(identifier, name)
    alternate = row.get('mtSummarySwitchOpen')
    if isinstance(alternate, dict) and isinstance(alternate.get('content'), str) and alternate['content'].strip():
        details['available_text_references']['transcript'] = _reference(identifier, 'transcript')
    if kind == 'summary':
        body = row.get('aiSummary') or {}
        if not isinstance(body, dict) or not isinstance(body.get('content'), str): raise DataAdapterError('no_extracted_text')
        text = _plain(body['content'], max_chars+1)
        clipped = len(text)>max_chars; text = text[:max_chars]
        details.update(content_origin='provider_stored_summary', text_scope='abstract',
            generation_basis='provider_ai_summary_field', original_document_available=False,
            source_field='aiSummary.content', machine_transcribed=False)
        warnings += ['abstract_not_full_text', 'alphapai_ai_summary_not_verbatim_original']
    else:
        key = 'mtSummary'
        body = row.get(key) or {}
        if not isinstance(body, dict) or not body.get('content'):
            key = 'mtSummarySwitchOpen'; body = row.get(key) or {}
        if not isinstance(body, dict): raise DataAdapterError('upstream_schema')
        text, segments, clipped, origin = _transcript(body.get('content'), max_chars)
        details.update(content_origin=origin, text_scope='source_excerpt', machine_transcribed=True,
            segments=segments, source_time_unit='unverified', source_field=key+'.content',
            generation_basis='machine_transcription_not_new_llm_generation')
        warnings += ['alphapai_transcript_completeness_unverified', 'alphapai_transcript_time_unit_unverified',
                     'machine_transcript_may_contain_errors']
        count = body.get('wordCount')
        if type(count) is int and count >= 0:
            details['provider_word_count'] = count
            details['returned_text_characters'] = len(text)
            details['word_count_not_character_count'] = True
        length = row.get('recLength')
        if type(length) is int and length >= 0: details['record_length_raw'] = length
    if not text: raise DataAdapterError('no_extracted_text')
    if clipped: warnings.append('text_truncated')
    details['locally_truncated'] = clipped
    return Document(make_doc_id(reference, hash_text(text)), reference, None, title, 'alphapai',
        SourceTier.MEDIA, EvidenceType.OPINION,
        'text/plain', published, reply.fetched_at, 'alphapai_stored_'+kind, text,
        text_hash=hash_text(text), warnings=warnings,
        extra={'adapter_mode':'live', 'publisher':publisher, 'generated':kind=='summary',
            'text_provider':'alphapai', 'source_text_trust':'untrusted', 'material_read':details,
            'text_origin':'provider_summary' if kind=='summary' else 'source_excerpt'})


def fetch_alphapai_document(reference: str, *, context, max_chars=20000, profile=None, client=None):
    """Read an explicit summary or transcript reference, retaining partial-text scope."""
    identifier, _ = _parse_reference(reference)
    if type(max_chars) is not int or not 1 <= max_chars <= 100000: raise ValueError('Invalid text limit')
    context.check_active()
    if context.account_scope != 'default': raise DataAdapterError('entitlement_denied')
    try: profile = profile if profile is not None else alphapai_profile()
    except SourceConfigError: raise DataAdapterError('source_config_error') from None
    if profile is None: raise DataAdapterError('no_credential')
    owned = client is None
    client = client if client is not None else AlphapaiClient(profile)
    try:
        reply = client.read('detail', {'id':identifier}, context=context)
        context.check_active()
        return _document(reference, reply, max_chars)
    finally:
        if owned: client.close()


def _time_citation(details, start, end):
    segments = [s for s in details.get('segments', []) if s['start_char'] < end and s['end_char'] > start]
    if not segments: return {}
    return {'machine_transcribed':True, 'source_time_unit':'unverified',
        'source_start':min(s['source_start'] for s in segments), 'source_end':max(s['source_end'] for s in segments),
        'speaker_roles':list(dict.fromkeys(s['speaker_role'] for s in segments))}
