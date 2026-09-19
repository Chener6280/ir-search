"""Gangtise material normalization with explicit summary/excerpt boundaries."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import unescape
import re

from .gangtise import GangtiseClient, gangtise_profile, _parse_reference, _identifier, _file_path
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
        return extract_html_document(value.encode(), 'https://open.gangtise.com/', max_chars=limit).text.strip()
    return unescape(value).strip()[:limit]


def _label(value, limit=2000):
    if value is None: return ''
    if not isinstance(value, str): raise DataAdapterError('upstream_schema')
    return re.sub(r'\s+', ' ', unescape(re.sub(r'<[^>]*>', '', value))).strip()[:limit]


def _time(value):
    if value is None or value == '': return None
    try:
        if type(value) in (int, float) or (isinstance(value, str) and value.isdigit()):
            n = float(value)
            if 946684800000 <= n <= 4102444800000: return datetime.fromtimestamp(n/1000, CST)
            if 946684800 <= n <= 4102444800: return datetime.fromtimestamp(n, CST)
            raise ValueError()
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d\d-\d\d(?:[ T]\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)?)?', value): raise ValueError()
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return dt.replace(tzinfo=CST) if dt.utcoffset() is None else dt.astimezone(CST)
    except (ValueError, TypeError, OverflowError, OSError): raise DataAdapterError('upstream_schema') from None


def _metadata(row, category):
    try: identifier = _identifier(row.get('rptId') if category == 'report' and row.get('rptId') is not None else row.get('id'))
    except DataAdapterError: raise DataAdapterError('upstream_schema') from None
    msg = row.get('msgText') if isinstance(row.get('msgText'), dict) else {}
    title = _label(row.get('title') or msg.get('title'))
    if not title and category == 'opinion': title = '岗底斯研究观点'
    if not title: raise DataAdapterError('upstream_schema')
    initiators = row.get('initiator') or []
    initiator = ' / '.join(dict.fromkeys(_label(r.get('partyName') or r.get('cnName'), 200)
        for r in initiators if isinstance(r, dict))) if isinstance(initiators, list) else ''
    publisher = _label(row.get('issuerStmt') or row.get('partyName') or row.get('brokerName'), 200) or initiator or 'unknown'
    published = _time(row.get('pubTime') if category == 'report' else row.get('msgTime'))
    return identifier, title, publisher, published


def _snippet(row, category, limit):
    if category == 'opinion':
        msg = row.get('msgText') if isinstance(row.get('msgText'), dict) else {}
        return _plain(msg.get('content') or msg.get('description'), limit)
    return _plain(row.get('brief') or row.get('details'), limit)


def _document(reference, reply, max_chars, *, client, context):
    category, identifier = _parse_reference(reference)
    row = reply.data
    rid, title, publisher, published = _metadata(row, category)
    if rid != identifier: raise DataAdapterError('upstream_schema')
    details = {'text_provider': 'gangtise', 'access_method': 'account_session', 'collection': category,
        'complete': False, 'source_text_trust': 'untrusted', 'cache_state': 'not_cached',
        'publisher_identity_verification': 'provider_label_only',
        'publication_basis': 'provider_pubTime' if category == 'report' else 'provider_msgTime',
        'source_schema_basis': 'official_web_client_contract', 'reference_stability': 'unverified_provider_identifier'}
    warnings = ['source_uri_not_web_url', 'not_full_text', 'gangtise_source_claims_not_independently_verified',
        'gangtise_reference_stability_unverified', 'gangtise_naive_time_assumed_asia_shanghai']
    if category == 'summary': warnings.append('gangtise_display_time_not_verified_publication_time')
    generated = False
    if category == 'summary':
        versions = row.get('msgText')
        if not isinstance(versions, list): raise DataAdapterError('upstream_schema')
        # Prefer the stored human-edited minutes when present; do not trigger new AI work.
        selected = None
        for usage in (5, 10, 7):
            for part in versions:
                if part.get('usage') not in (usage, str(usage)): continue
                try: _file_path(part.get('url'))
                except DataAdapterError: continue
                selected = part; break
            if selected is not None: break
        if selected is None: raise DataAdapterError('gangtise_format_unavailable')
        usage = int(selected['usage'])
        value = client._summary_text(identifier, selected['url'], context=context)
        text = _plain(value, max_chars+1)
        generated = usage == 10
        details.update(source_field='msgText[usage='+str(usage)+'].url', provider_usage=usage,
            content_origin='provider_stored_summary' if usage in {5,10} else 'provider_stored_material',
            text_scope='abstract' if usage in {5,10} else 'source_excerpt',
            generation_basis='provider_ai_minutes' if generated else 'provider_human_edited_minutes' if usage==5 else 'unverified_provider_material',
            original_document_available=False)
        warnings.append('gangtise_minutes_not_verbatim_transcript')
        if generated: warnings.append('gangtise_ai_summary_not_verbatim_original')
    elif category == 'report':
        text = _plain(row.get('brief') or row.get('details'), max_chars+1)
        details.update(content_origin='provider_stored_summary', text_scope='abstract',
            source_field='brief' if row.get('brief') else 'details', original_document_available=False,
            generation_basis='unverified_provider_abstract')
        warnings += ['abstract_not_full_text', 'gangtise_report_pdf_not_read']
    else:
        msg = row.get('msgText') if isinstance(row.get('msgText'), dict) else {}
        text = _plain(msg.get('content') or msg.get('description'), max_chars+1)
        summary = not msg.get('content')
        details.update(content_origin='provider_stored_summary' if summary else 'provider_opinion_excerpt',
            text_scope='abstract' if summary else 'source_excerpt',
            source_field='msgText.description' if summary else 'msgText.content')
        if not row.get('title') and not msg.get('title'): warnings.append('gangtise_title_is_display_label')
        warnings.append('gangtise_opinion_completeness_unverified')
    if not text: raise DataAdapterError('no_extracted_text')
    clipped = len(text)>max_chars; text = text[:max_chars]
    details['locally_truncated'] = clipped
    if clipped: warnings.append('text_truncated')
    if row.get('isPreview') is True: warnings.append('gangtise_account_preview_only')
    return Document(make_doc_id(reference, hash_text(text)), reference, None, title, 'gangtise',
        SourceTier.MEDIA, EvidenceType.OPINION, 'text/plain', published, reply.fetched_at,
        'gangtise_stored_'+category, text, text_hash=hash_text(text), warnings=warnings,
        extra={'adapter_mode': 'live', 'publisher': publisher, 'generated': generated,
            'text_provider': 'gangtise', 'source_text_trust': 'untrusted', 'material_read': details,
            'text_origin': 'provider_summary' if details['text_scope']=='abstract' else 'source_excerpt'})


def fetch_gangtise_document(reference: str, *, context, max_chars=20000, profile=None, client=None):
    """Read an explicit Gangtise reference; never replace unavailable text with a snippet."""
    category, identifier = _parse_reference(reference)
    if type(max_chars) is not int or not 1 <= max_chars <= 100000: raise ValueError('Invalid text limit')
    context.check_active()
    if context.account_scope != 'default': raise DataAdapterError('entitlement_denied')
    try: profile = profile if profile is not None else gangtise_profile()
    except SourceConfigError: raise DataAdapterError('source_config_error') from None
    if profile is None: raise DataAdapterError('no_credential')
    owned = client is None
    client = client if client is not None else GangtiseClient(profile)
    try:
        reply = client.read('detail', {'category':category, 'id':identifier}, context=context)
        result = _document(reference, reply, max_chars, client=client, context=context)
        context.check_active(); return result
    finally:
        if owned: client.close()
