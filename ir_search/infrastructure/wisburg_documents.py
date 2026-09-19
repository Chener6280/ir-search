"""Stored Wisburg articles, commentary and summaries with explicit evidence scope."""
from __future__ import annotations

from ir_search.documents.models import Document, hash_text, make_doc_id
from ir_search.infrastructure.credentials import SourceConfigError, wisburg_profile
from ir_search.infrastructure.wisburg import WisburgClient, DETAIL_TOOLS, _parse_reference, _parse_detail
from ir_search.models import EvidenceType, SourceTier
from ir_search.registry import DataAdapterError


def _details(kind):
    summary = kind == 'report'
    return {'content_origin':'provider_stored_summary' if summary else 'provider_article_text',
            'text_scope':'abstract' if summary else 'extracted_text',
            'original_document_available':False if summary else None,
            'generation_basis':'conservative_provider_ai_assisted_summary_classification' if summary else 'not_asserted',
            'source_text_trust':'untrusted', 'text_provider':'wisburg'}


def _warnings(kind):
    base = ['source_uri_not_web_url', 'wisburg_publication_time_not_original_document_date',
            'wisburg_source_claims_not_independently_verified', 'images_not_downloaded_or_ocr']
    if kind == 'report':
        base += ['abstract_not_full_text', 'wisburg_original_document_not_provided',
                 'wisburg_summary_may_be_ai_generated', 'wisburg_summary_not_verbatim_original']
    if kind == 'mikko': base += ['personal_opinion_not_institutional_research']
    return base


def fetch_wisburg_document(reference: str, *, context, max_chars=20000, profile=None, client=None):
    """Read an explicit wisburg://report|article|mikko/ID; no inferred web URL."""
    kind, identifier = _parse_reference(reference)
    if type(max_chars) is not int or not 1 <= max_chars <= 100000: raise ValueError('Invalid text limit')
    context.check_active()
    if context.account_scope != 'default': raise DataAdapterError('entitlement_denied')
    try: profile = profile if profile is not None else wisburg_profile()
    except SourceConfigError: raise DataAdapterError('source_config_error') from None
    if profile is None: raise DataAdapterError('no_credential')
    client = client if client is not None else WisburgClient(profile)
    reply = client.read(DETAIL_TOOLS[kind], {'id':identifier}, context=context)
    context.check_active()
    row = _parse_detail(reply.text, kind, identifier)
    warnings = _warnings(kind)
    if len(row.text)>max_chars: warnings.append('text_truncated')
    text = row.text[:max_chars]
    return Document(make_doc_id(reference,hash_text(text)), reference, None, row.title, 'wisburg',
        SourceTier.MEDIA, EvidenceType.OPINION, 'text/markdown', row.published_at, reply.fetched_at,
        'wisburg_stored_summary' if kind=='report' else 'wisburg_article_markdown', text,
        text_hash=hash_text(text), warnings=warnings,
        extra={'adapter_mode':'live', 'publisher':'智堡 Wisburg', 'generated':kind=='report',
               'text_provider':'wisburg', 'source_text_trust':'untrusted',
               'material_read':_details(kind), 'text_origin':'provider_summary' if kind=='report' else 'extracted_text'})
