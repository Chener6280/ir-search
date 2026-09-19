"""Resolve explicit community references without exposing signed attachment URLs."""
from __future__ import annotations

from dataclasses import replace
import re
from urllib.parse import urljoin, urlsplit

from ir_search.adapters.zsxq_materials import _candidate
from ir_search.contracts.materials import TextScope
from ir_search.documents.models import Document, hash_text, make_doc_id
from ir_search.documents.pdf import extract_pdf_document
from ir_search.infrastructure.credentials import zsxq_profile
from ir_search.infrastructure.public_web import _request, _url
from ir_search.infrastructure.zsxq import ZsxqClient, _error_code
from ir_search.registry import DataAdapterError


def _reference(value):
    if not isinstance(value, str): raise DataAdapterError("unsupported")
    match = re.fullmatch(r"zsxq://(topic|file)/([1-9][0-9]{0,29})/([1-9][0-9]{0,29})(?:/([1-9][0-9]{0,29}))?", value)
    if not match or (match[1] == "file") != bool(match[4]): raise DataAdapterError("unsupported")
    return match[1], match[2], match[3], match[4]


def _download(url, *, context):
    # Only the official file host observed through the authenticated download API.
    # Query signatures stay in memory and are never attached to Document or errors.
    current, seen = url, set()
    for _ in range(4):
        parsed, host, _ = _url(current, signed_download=True)
        if host != "files.zsxq.com" or current in seen: raise DataAdapterError("blocked_url")
        seen.add(current)
        reply = _request(current, context=context, signed_download=True)
        if reply.status == 200:
            if not reply.body.startswith(b"%PDF-"): raise DataAdapterError("web_content_unsupported")
            return reply.body
        if not reply.location: raise DataAdapterError("blocked_url")
        current = urljoin(current, reply.location)
    raise DataAdapterError("web_redirect_limit")


def fetch_zsxq_document(reference: str, *, context, max_chars=20000, profile=None, client=None, downloader=None):
    """Read a topic or its verified PDF attachment using only the official account."""
    if type(max_chars) is not int or not 1 <= max_chars <= 100000: raise ValueError("Invalid text limit")
    mode, gid, tid, fid = _reference(reference)
    profile = profile if profile is not None else zsxq_profile()
    if profile is None: raise DataAdapterError("no_credential")
    if context.account_scope != "default" or (profile.group_ids and gid not in profile.group_ids):
        raise DataAdapterError("entitlement_denied")
    client = client if client is not None else ZsxqClient(profile)
    response = client.read("get_topic_info", {"topic_id": tid}, context=context)
    topic = response.data.get("topic")
    candidate = _candidate(topic, gid, response.fetched_at, max_chars=max_chars, full=True)
    if candidate.source_document_id != tid: raise DataAdapterError("upstream_schema")
    if mode == "topic":
        if not candidate.text.strip(): raise DataAdapterError("no_extracted_text")
        doc = Document(make_doc_id(reference, hash_text(candidate.text)), reference, candidate.original_url,
            candidate.title, "zsxq", candidate.provenance.source_tier, candidate.provenance.evidence_type,
            "html", candidate.published_at, candidate.provenance.fetched_at, "zsxq_official_topic",
            candidate.text, text_hash=hash_text(candidate.text), warnings=list(candidate.warnings),
            extra={"adapter_mode": "live", "publisher": candidate.provenance.publisher,
                   "sections": [s.to_dict() for s in candidate.sections],
                   "attachments": [a.to_dict() for a in candidate.attachments], "collection_id": gid})
        if candidate.text_scope != TextScope.EXTRACTED_TEXT:
            doc.extra["text_origin"] = "source_excerpt"
            doc.warnings.append("question_answer_roles_unverified")
        return doc
    attachment = next((a for a in candidate.attachments if a.source_ref == reference), None)
    if attachment is None: raise DataAdapterError("entitlement_denied")
    if attachment.media_type != "pdf": raise DataAdapterError("web_content_unsupported")
    if attachment.size_bytes is not None and attachment.size_bytes > 8 * 1024 * 1024:
        raise DataAdapterError("response_too_large")
    from importlib.util import find_spec
    if find_spec("fitz") is None: raise DataAdapterError("dependency_missing")
    reply = client.read("call_zsxq_api", {"method": "GET", "path": f"/v2/files/{fid}/download_url"}, context=context)
    body = reply.data.get("body")
    if reply.data.get("status_code") != 200 or not isinstance(body, dict) or body.get("succeeded") is not True:
        raise DataAdapterError(_error_code(message=body))
    data = body.get("resp_data")
    if not isinstance(data, dict): raise DataAdapterError("upstream_schema")
    download_url = data.get("download_url")
    parsed, host, _ = _url(download_url, signed_download=True)
    if host != "files.zsxq.com": raise DataAdapterError("blocked_url")
    raw = (downloader or _download)(download_url, context=context)
    context.check_active()
    if not isinstance(raw, bytes) or len(raw) > 8 * 1024 * 1024: raise DataAdapterError("response_too_large")
    if not raw.startswith(b"%PDF-"): raise DataAdapterError("web_content_unsupported")
    document = extract_pdf_document(raw, reference, max_chars=max_chars)
    if document.errors:
        raise DataAdapterError("dependency_missing" if any("not installed" in e for e in document.errors) else "upstream_schema")
    if not document.text.strip(): raise DataAdapterError("no_extracted_text")
    context.check_active()
    # Publication of the containing post is not the PDF's own publication date.
    return replace(document, url=reference, canonical_url=candidate.original_url, title=attachment.name,
        source="zsxq", source_tier=candidate.provenance.source_tier, evidence_type=candidate.provenance.evidence_type,
        published_at=None, fetched_at=reply.fetched_at,
        warnings=document.warnings + ["attachment_original_publisher_unverified", "attachment_publication_unknown"],
        extra={"adapter_mode": "live", "publisher": candidate.provenance.publisher, "collection_id": gid,
               "source_text_trust": "untrusted"})
