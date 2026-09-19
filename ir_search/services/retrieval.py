"""Read explicit URLs using the existing fetch/extract implementation.

Discovery stays in search; no hidden live searches, summaries or snippet fallback.
Speech recognition is an explicit audio extraction option with its own budget.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
from typing import Optional
from urllib.parse import parse_qsl, urlsplit

from ir_search.context import RequestContext, RequestStopped
from ir_search.contracts import (
    AdapterMode, Diagnostic, Material, MaterialBundle, MaterialRequest, Provenance, Status,
)
from ir_search.infrastructure.web_documents import read_web_document, _VALID
from ir_search.infrastructure.public_web import _allowed_domain
from ir_search.documents.safety import is_url_allowed
from ir_search.evidence import extract_evidence
from ir_search.models import EvidenceType, FailureKind, SourceAuthority
from ir_search.registry import DataAdapterError


def retrieve(request: MaterialRequest, *, context: Optional[RequestContext] = None) -> MaterialBundle:
    """Return original text and citeable spans, preserving per-URL failures and gaps."""
    if not isinstance(request, MaterialRequest):
        raise ValueError("retrieve requires a MaterialRequest")
    for url in request.urls:
        _check_credential_free_url(url)
    context = context if context is not None else RequestContext(max_operations=100)
    result = MaterialBundle(request, context.request_id)
    queue = list(request.urls)
    parents = {}
    if request.follow_links:
        result.discovery = {"max_depth": 1, "limit": request.follow_links, "queued_links": 0, "complete": False,
                            "domains": list(request.link_domains)}
    for index, url in enumerate(queue):
        try:
            context.check_active()
            community_reference = url.startswith("zsxq://")
            ima_reference = url.startswith("ima://")
            wisburg_reference = url.startswith("wisburg://")
            alphapai_reference = url.startswith('alphapai://')
            gangtise_reference = url.startswith('gangtise://')
            xhs_reference = url.startswith('xhs://') or urlsplit(url).hostname == 'www.xiaohongshu.com'
            sec_reference = urlsplit(url).hostname in {'www.sec.gov', 'sec.gov'} and urlsplit(url).path.startswith('/Archives/')
            wechat_reference = urlsplit(url).hostname == "mp.weixin.qq.com" and url not in parents
            from ir_search.infrastructure.platform_documents import _platform_host, _platform_url
            platform_reference = _platform_host(url)
            platform = _platform_url(url)[0] if platform_reference else None
            video_reference = platform in {'bilibili', 'youtube'}
            audio_reference = platform == 'xiaoyuzhou'
            provider_reference = url.startswith("jydb://") or community_reference or ima_reference or wisburg_reference or alphapai_reference or gangtise_reference or xhs_reference
            if not provider_reference and not is_url_allowed(url).allowed:
                result.diagnostics.append(_diag(index, "blocked_url", FailureKind.BLOCKED_BY_POLICY))
                continue
            if provider_reference or wechat_reference:
                context.begin_operation()
            if xhs_reference:
                from ir_search.infrastructure.xhs_documents import fetch_xhs_document
                document = fetch_xhs_document(url, max_chars=request.max_chars, context=context,
                    comment_limit=request.xhs_comment_limit, cache_mode=request.xhs_cache_mode)
            elif gangtise_reference:
                from ir_search.infrastructure.gangtise_documents import fetch_gangtise_document
                document = fetch_gangtise_document(url, max_chars=request.max_chars, context=context)
            elif alphapai_reference:
                from ir_search.infrastructure.alphapai_documents import fetch_alphapai_document
                document = fetch_alphapai_document(url, max_chars=request.max_chars, context=context)
            elif sec_reference:
                from ir_search.infrastructure.sec import read_sec_document
                document = read_sec_document(url, max_chars=request.max_chars, context=context)
            elif wisburg_reference:
                from ir_search.infrastructure.wisburg_documents import fetch_wisburg_document
                document = fetch_wisburg_document(url, max_chars=request.max_chars, context=context)
            elif ima_reference:
                from ir_search.infrastructure.ima_documents import fetch_ima_document
                document = fetch_ima_document(url, max_chars=request.max_chars, context=context)
            elif community_reference:
                from ir_search.infrastructure.zsxq_documents import fetch_zsxq_document
                document = fetch_zsxq_document(url, max_chars=request.max_chars, context=context)
            elif provider_reference:
                from ir_search.adapters.jydb import build_jydb_adapter
                document = build_jydb_adapter().fetch_document(url, max_chars=request.max_chars, context=context)
            elif wechat_reference:
                from ir_search.infrastructure.wechat import fetch_wechat_document
                document = fetch_wechat_document(url, max_chars=request.max_chars, context=context,
                    mode=request.web_read_mode, cache_mode=request.wechat_cache_mode)
            elif platform_reference:
                if audio_reference:
                    from ir_search.infrastructure.audio_documents import read_audio_document
                    document = read_audio_document(url, context=context, max_chars=request.max_chars,
                        audio_mode=request.audio_mode, audio_start_seconds=request.audio_start_seconds,
                        audio_max_seconds=request.audio_max_seconds, audio_window_count=request.audio_window_count)
                elif video_reference:
                    from ir_search.infrastructure.video_documents import read_video_document
                    document = read_video_document(url, context=context, max_chars=request.max_chars, languages=request.video_languages)
                else:
                    from ir_search.infrastructure.community_documents import read_community_document
                    document = read_community_document(url, context=context, max_chars=request.max_chars)
            else:
                document = read_web_document(url, max_chars=request.max_chars, context=context, mode=request.web_read_mode,
                    allowed_domains=request.link_domains if url in parents else ())
            context.check_active()
            _check_credential_free_url(document.url)
            read_details = document.extra.get("web_read", {})
            if wisburg_reference or sec_reference or alphapai_reference or gangtise_reference or xhs_reference:
                read_details = document.extra.get('material_read', {})
            if read_details:
                result.reads.append({"input_index": index, "url": url, "discovered_from": parents.get(url), **read_details})
            if audio_reference and read_details.get('window_failure_code'):
                code = read_details['window_failure_code']
                result.diagnostics.append(_diag(index, code, DataAdapterError.KINDS.get(code, FailureKind.UNKNOWN)))
            content_state = read_details.get("content_state")
            metadata_video = (video_reference or audio_reference) and content_state == 'metadata_only' and not document.text and not document.errors
            if content_state and content_state not in _VALID and not metadata_video:
                code = "web_content_" + content_state
                result.diagnostics.append(_diag(index, code, DataAdapterError.KINDS.get(code, FailureKind.UPSTREAM_SCHEMA)))
                for attempt in read_details.get("attempts", []):
                    if attempt.get("state") in DataAdapterError.KINDS:
                        result.diagnostics.append(_diag(index, attempt["state"], DataAdapterError.KINDS[attempt["state"]]))
                continue
            if document.errors:
                code = "document_fetch_failed"
                failure = FailureKind.NETWORK
                if any("blocked_by_policy" in str(error) for error in document.errors):
                    code, failure = "blocked_url", FailureKind.BLOCKED_BY_POLICY
                elif "pymupdf" in document.extraction_method:
                    code, failure = "document_extraction_failed", FailureKind.UPSTREAM_SCHEMA
                result.diagnostics.append(_diag(index, code, failure))
                if document.text.strip():
                    continue
            if not document.text.strip() and not metadata_video:
                result.diagnostics.append(_diag(index, "no_extracted_text", FailureKind.UPSTREAM_SCHEMA))
                continue
            mode = AdapterMode(document.extra.get("adapter_mode") or "live")
            if mode != AdapterMode.LIVE:
                result.diagnostics.append(Diagnostic("non_live_document_blocked", "fetch", failure_kind=FailureKind.BLOCKED_BY_POLICY,
                                                     adapter_mode=mode, message=f"input_index={index}"))
                continue
            origin = "extracted_text"
            if metadata_video:
                origin = 'metadata_only'
                code = read_details.get('caption_status', 'audio_show_notes_unavailable' if audio_reference else 'video_captions_unavailable')
                result.diagnostics.append(_diag(index, code, DataAdapterError.KINDS.get(code, FailureKind.UPSTREAM_SCHEMA)))
            if audio_reference and document.text:
                origin = 'asr_transcript' if read_details.get('content_origin') == 'volcengine_asr' else 'source_excerpt'
            if community_reference and document.extra.get("text_origin") == "source_excerpt":
                origin = "source_excerpt"
            warnings = []
            if read_details:
                warnings.extend(w for w in document.warnings if isinstance(w, str) and w.startswith("web_")
                                and w.replace("_", "").isalnum())
                warnings.extend(w for w in document.warnings if w in {"publication_date_conflict", "publication_date_invalid",
                                                                     "unencrypted_public_document"})
                if document.published_at and document.published_at.utcoffset() is None:
                    warnings.append("publication_time_precision_unverified")
            if document.warnings:
                warnings.append("extraction_reported_warnings")
                if provider_reference:
                    allowed = {"source_uri_not_web_url", "vendor_text_not_original_file", "publication_time_precision_unverified",
                               "publisher_unknown", "text_truncated"}
                    warnings.extend(w for w in document.warnings if w in allowed)
                if community_reference or wechat_reference or ima_reference or wisburg_reference or platform_reference or sec_reference or alphapai_reference or gangtise_reference or xhs_reference:
                    warnings.extend(w for w in document.warnings if isinstance(w, str) and w.replace("_", "").isalnum())
            if document.published_at is None:
                warnings.append("published_date_unknown")
            if document.content_type == "snippet" or "snippet" in document.extraction_method:
                origin = "search_snippet"
                warnings.append("not_full_text")
            if origin == "extracted_text" and "ocr" in document.extraction_method.lower():
                origin = "ocr_text"
            if document.extra.get("generated"):
                origin = "generated_text"
                warnings.append("generated_content")
            if (wisburg_reference or alphapai_reference or gangtise_reference) and read_details.get('content_origin') == 'provider_stored_summary':
                origin = 'provider_summary'
            if (alphapai_reference and read_details.get('machine_transcribed')) or (gangtise_reference and read_details.get('text_scope') == 'source_excerpt'):
                origin = 'source_excerpt'
            if any("truncat" in str(w).lower() and "link" not in str(w).lower() for w in document.warnings) or len(document.text) > request.max_chars:
                warnings.append("text_truncated")
            warnings = list(dict.fromkeys(warnings))
            document = replace(
                document, text=document.text[:request.max_chars], warnings=list(warnings),
                evidence_type=EvidenceType.OPINION if document.extra.get("generated") else document.evidence_type,
                extra={**document.extra, "adapter_mode": mode.value},
            )
            sections = document.extra.get("sections", []) if community_reference or xhs_reference else []
            if sections:
                spans = []
                for section in sections:
                    start, end = section["start_char"], section["end_char"]
                    piece = replace(document, text=document.text[start:end])
                    for span in extract_evidence(piece, request.question, max_spans=request.max_spans):
                        spans.append(replace(span, start_char=span.start_char + start, end_char=span.end_char + start,
                            page=None, extra={**span.extra, "source_role": section["role"], "author": section["author"],
                                              "section_source_ref": section["source_ref"], "section_published_at": section["published_at"]}))
                spans = sorted(spans, key=lambda span: -span.relevance_score)[:request.max_spans]
            else:
                spans = extract_evidence(document, request.question, max_spans=request.max_spans)
            context.check_active()
            text_hash = hashlib.sha256(document.text.encode("utf-8")).hexdigest()
            if read_details:
                previous = request.previous_text_hashes.get(url, request.previous_text_hashes.get(document.url))
                read_details.update(change_state="unchanged" if previous == text_hash else "changed" if previous else "new",
                    change_basis="returned_text_sha256_same_read_options_required", discovered_from=parents.get(url))
                result.reads[-1].update(read_details)
            if any(span.text != document.text[span.start_char:span.end_char] for span in spans):
                result.diagnostics.append(_diag(index, "citation_offset_mismatch", FailureKind.UPSTREAM_SCHEMA))
                continue
            spans = [replace(span, extra={**span.extra, "text_hash": text_hash}) for span in spans]
            if video_reference:
                from ir_search.infrastructure.video_documents import _time_citation
                spans = [replace(span, extra={**span.extra, **_time_citation(read_details, document.url, document.text,
                         span.start_char, span.end_char)}) for span in spans]
            if audio_reference:
                from ir_search.infrastructure.audio_documents import _audio_citation
                spans = [replace(span, extra={**span.extra, **_audio_citation(read_details, document.text,
                         span.start_char, span.end_char)}) for span in spans]
            if alphapai_reference and read_details.get('machine_transcribed'):
                from ir_search.infrastructure.alphapai_documents import _time_citation
                spans = [replace(span, extra={**span.extra, **_time_citation(read_details,
                         span.start_char, span.end_char)}) for span in spans]
            if not spans:
                warnings.append("no_relevant_spans")
            from ir_search.institutions import _institution_for_url
            institution = _institution_for_url(document.url) if not (provider_reference or community_reference or ima_reference) else None
            official_authority = (SourceAuthority.REGULATOR if institution.category == 'regulator' else SourceAuthority.COMPANY) if institution else SourceAuthority.UNKNOWN
            from ir_search.infrastructure.official_materials import _official_identity
            official_identity = _official_identity(document.url)
            if official_identity:
                official_authority = SourceAuthority.OFFICIAL_FILING if official_identity[0] == 'hkex' else SourceAuthority.COMPANY
            provenance = Provenance(
                provider=document.source, publisher=document.extra.get("publisher") or (urlsplit(document.url).hostname if institution else 'unknown'),
                fetched_at=document.fetched_at, source_tier=document.source_tier,
                evidence_type=document.evidence_type,
                authority=SourceAuthority.OFFICIAL_FILING if sec_reference else SourceAuthority.UNKNOWN if ima_reference else SourceAuthority.UGC if community_reference or platform_reference or xhs_reference else SourceAuthority.DATA_VENDOR if provider_reference else official_authority,
                adapter_mode=mode, generated=bool(document.extra.get("generated")),
            )
            result.materials.append(Material(
                doc_id=document.doc_id, url=document.url, title=document.title, text=document.text,
                provenance=provenance, published_at=document.published_at,
                extraction_method=document.extraction_method, text_origin=origin,
                evidence_spans=[span.to_dict() for span in spans], warnings=warnings,
                original_url=document.canonical_url if community_reference or wechat_reference or ima_reference or platform_reference or sec_reference or xhs_reference else None,
                text_provider=document.extra.get("text_provider") if wechat_reference or ima_reference or wisburg_reference or alphapai_reference or gangtise_reference or xhs_reference else None,
                text_hash=text_hash, links=document.extra.get("public_links", []), read_details=read_details,
                article=document.extra.get('article', {}),
                sections=sections, attachments=document.extra.get("attachments", []) if community_reference else [],
            ))
            if request.archive_dir:
                from .material_archive import export_material
                material = result.materials[-1]
                material.archive = export_material(material, request.archive_dir, download_images=request.archive_images,
                    max_images=request.max_archive_images, context=context)
                if material.archive['status'] not in {'ok', 'reused'}:
                    result.diagnostics.append(_diag(index, 'material_archive_incomplete', FailureKind.NONE))
            if warnings:
                result.diagnostics.append(_diag(index, "material_has_caveats", FailureKind.NONE))
            if request.follow_links and url not in parents and read_details:
                for link in document.extra.get("public_links", []):
                    target = link["url"]
                    if len(parents) >= request.follow_links:
                        break
                    if target not in queue and _allowed_domain(urlsplit(target).hostname or "", request.link_domains):
                        queue.append(target)
                        parents[target] = url
                result.discovery["queued_links"] = len(parents)
        except RequestStopped as exc:
            result.diagnostics.append(_diag(index, exc.code, exc.failure_kind))
            break
        except DataAdapterError as exc:
            result.diagnostics.append(_diag(index, exc.code, exc.failure_kind))
        except Exception:
            result.diagnostics.append(_diag(index, "document_processing_failed", FailureKind.UNKNOWN))
    if result.materials:
        result.status = Status.PARTIAL if result.diagnostics else Status.OK
    return result


def _diag(index, code, failure_kind):
    return Diagnostic(code, "fetch", failure_kind=failure_kind, message=f"input_index={index}")


def _check_credential_free_url(url):
    try:
        parsed = urlsplit(url)
        credential_keys = {"key", "apikey", "api_key", "token", "access_token", "password", "secret", "authorization", "xsec_token", "xsectoken"}
        invalid = parsed.username or parsed.password or any(k.lower() in credential_keys for k, _ in parse_qsl(parsed.query))
    except ValueError:
        raise ValueError("Invalid URL") from None
    if invalid:
        raise ValueError("Use credential-free URLs; authentication belongs in a provider client")
