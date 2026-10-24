"""Public archival API for full-enumeration exports by external archivers.

Public archival API, read-only, bounded: every entry point enumerates records
without keywords, honors RequestContext budgets, returns JSON-serializable
dicts and never exposes credentials or signed URLs. Returned records are
metadata-level; body text is opt-in via include_text, and full attributed text
remains available through the existing retrieve() service. Original file bytes
are returned only by retrieve_asset, which keeps the byte payload in a single
documented ``content`` field while the rest of its dict stays JSON-serializable.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import re

from ir_search.context import RequestContext, RequestStopped
from ir_search.contracts import Diagnostic
from ir_search.contracts.collection_export import CollectionDescriptor, TimelineComment, TimelineItem
from ir_search.contracts.materials import _day
from ir_search.infrastructure import ima_documents, wisburg as wisburg_infra, zsxq_documents
from ir_search.infrastructure.material_cursor import _continuation, _decode, _fingerprint, _slice
from ir_search.infrastructure.recovery import _stop_source
from ir_search.material_registry import build_material_registry
from ir_search.models import FailureKind
from ir_search.registry import DataAdapterError

_PROVIDERS = ("zsxq", "ima", "wisburg")
_CST = timezone(timedelta(hours=8))
_NUMERIC_ID = re.compile(r"[1-9][0-9]{0,29}")
_MAX_DIRECTORY_COLLECTIONS = 200
_MAX_ITEMS_CAP = 1000
_MAX_PAGES_HARD = 100
_MAX_COMMENT_PAGES = 50
_MAX_COMMENT_DEPTH = 2
_ZSXQ_DOWNLOAD_HOST = "files.zsxq.com"
_IMA_MEDIA_TYPE_LABELS = {1: "pdf", 2: "webpage", 3: "docx", 4: "pptx", 5: "xlsx", 6: "wechat",
                          7: "txt", 9: "image", 11: "note", 13: "txt", 20: "html"}
_IMA_DOWNLOAD_MEDIA_TYPES = {1, 3, 4, 5, 7, 9, 13, 20}
_IMA_MEDIA_LABELS = {
    "pdf": "application/pdf",
    "webpage": "text/html",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "wechat": "text/html",
    "txt": "text/plain",
    "note": "text/markdown",
    "html": "text/html",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "image": "image/*",
}


@dataclass(frozen=True)
class _ExportScope:
    """Fingerprint-compatible binding for continuation cursors of one export call."""
    question: str
    symbols: tuple
    entities: tuple
    keywords: tuple
    published_start: date
    published_end: date
    period_start: object = None
    period_end: object = None
    material_types: tuple = ()


def _diag(code, provider=None, failure=FailureKind.NONE):
    return Diagnostic(code, "collection_export", provider, failure_kind=failure)


def _diag_dicts(diagnostics):
    return [d.to_dict() for d in diagnostics]


def _context(context):
    return context if context is not None else RequestContext(max_operations=100)


def _registry(registry):
    return registry if registry is not None else build_material_registry()


def _adapter(registry, provider, context):
    entries = {adapter.name: adapter for adapter in registry.entries(account_scope=context.account_scope)}
    adapter = entries.get(provider)
    if adapter is None:
        raise ValueError(f"provider {provider!r} is not registered for account scope {context.account_scope!r}")
    return adapter


def _client(adapter, provider):
    """Reuse the adapter's injected client, or construct the official one from its profile."""
    client = getattr(adapter, "_client", None)
    if client is not None:
        return client
    factory = getattr(adapter, "_client_factory", None)
    profile = getattr(adapter, "_profile", None)
    if factory is not None and profile is not None:
        return factory(profile)
    if profile is None:
        raise ValueError(f"adapter for provider {provider!r} exposes no usable client")
    if provider == "ima":
        from ir_search.infrastructure.ima import IMAClient
        return IMAClient(profile)
    if provider == "wisburg":
        from ir_search.infrastructure.wisburg import WisburgClient
        return WisburgClient(profile)
    raise ValueError(f"adapter for provider {provider!r} exposes no usable client")


def _require_provider(provider):
    if provider not in _PROVIDERS:
        raise ValueError(f"Unknown material provider: {provider!r}")


def _window(published_start, published_end):
    start, end = _day(published_start), _day(published_end)
    if start is None or end is None:
        raise ValueError("published_start and published_end are required")
    if (end - start).days > 366:
        raise ValueError("Publication window must be at most 367 calendar days")
    return start, end


def _scope(start, end):
    return _ExportScope("", (), (), (), start, end)


def _resume(cursor, provider, collection, scope, context):
    """Decode and bind-check one continuation cursor for this exact export scope."""
    if cursor is None:
        return None
    state = _decode(cursor)
    if state["provider"] != provider or state["collection"] != collection:
        raise DataAdapterError("invalid_cursor")
    if state["binding"] != _fingerprint(scope, context.account_scope):
        raise DataAdapterError("invalid_cursor")
    return state


def _page_budget(max_items, page_size):
    return min(_MAX_PAGES_HARD, (max_items + page_size - 1) // page_size + 1)


def _int_or_none(value):
    return value if type(value) is int else None


def _stop_failure(exc):
    return exc.failure_kind if isinstance(exc, RequestStopped) else FailureKind.NONE


def _stopped_coverage(exc):
    return "budget_exhausted" if isinstance(exc, RequestStopped) else "upstream_stopped"


def list_material_collections(provider, *, registry=None, context=None) -> dict:
    """Public archival API, read-only, bounded: list collections visible to the account.

    zsxq reads the official group directory, ima enumerates knowledge bases with an
    empty query (bounded by the operation budget and a hard cap), and wisburg has no
    account-level directory so its nine stored categories are returned as a static
    catalog. The result is always a JSON-serializable dict, also for empty accounts.
    """
    _require_provider(provider)
    context = _context(context)
    diagnostics = []
    if provider == "wisburg":
        diagnostics.append(_diag("static_catalog_account_check_via_timeline_export", provider))
        collections = [CollectionDescriptor(collection_id=key, name=key, category=key)
                       for key in wisburg_infra.LIST_TOOLS]
        return {"schema_version": "1.0", "provider": provider,
                "collections": [row.to_dict() for row in collections],
                "collection_count": len(collections), "exhaustive": True,
                "provider_full_export_supported": True,
                "diagnostics": _diag_dicts(diagnostics)}
    registry = _registry(registry)
    adapter = _adapter(registry, provider, context)
    client = _client(adapter, provider)
    collections = []
    exhaustive = False
    try:
        if provider == "zsxq":
            response = client.groups(context=context)
            skipped = 0
            for row in response.data.get("groups") or []:
                try:
                    collections.append(_zsxq_directory_row(row))
                except (DataAdapterError, ValueError, TypeError):
                    skipped += 1
            if skipped:
                diagnostics.append(_diag("invalid_collection_record", provider, FailureKind.UPSTREAM_SCHEMA))
            exhaustive = not skipped and len(collections) < 200
            if len(collections) >= 200:
                diagnostics.append(_diag("collection_directory_not_exhaustive", provider))
        else:
            from ir_search.adapters.ima_materials import _plain
            from ir_search.infrastructure.ima import _id as _ima_id, _page
            cursor, seen = "", set()
            while len(collections) < _MAX_DIRECTORY_COLLECTIONS:
                context.check_active()
                reply = client.read("search_knowledge_base", {"query": "", "cursor": cursor, "limit": 20},
                                    context=context)
                rows, more, next_cursor = _page(reply.data, "info_list")
                for row in rows:
                    kid = _ima_id(row.get("kb_id", row.get("id")))
                    if kid in seen:
                        continue
                    seen.add(kid)
                    name = _plain(row.get("kb_name", row.get("name")))
                    if not name or len(name) > 1000:
                        raise DataAdapterError("upstream_schema")
                    status = row.get("status")
                    collections.append(CollectionDescriptor(
                        collection_id=kid, name=name,
                        status=status if isinstance(status, (str, int)) and not isinstance(status, bool) else None))
                if more is False:
                    exhaustive = True
                    break
                if more is None:
                    diagnostics.append(_diag("ima_directory_pagination_unknown", provider))
                    break
                if not next_cursor or next_cursor == cursor:
                    diagnostics.append(_diag("ima_pagination_stalled", provider, FailureKind.UPSTREAM_SCHEMA))
                    break
                cursor = next_cursor
            if len(collections) >= _MAX_DIRECTORY_COLLECTIONS:
                diagnostics.append(_diag("ima_directory_cap_reached", provider, FailureKind.BUDGET_EXHAUSTED))
            elif not exhaustive and not diagnostics:
                diagnostics.append(_diag("ima_directory_not_exhaustive", provider))
            diagnostics.append(_diag("ima_full_file_enumeration_unverified", provider))
    except (DataAdapterError, RequestStopped) as exc:
        diagnostics.append(_diag(exc.code, provider, _stop_failure(exc)))
        exhaustive = False
    return {"schema_version": "1.0", "provider": provider,
            "collections": [row.to_dict() for row in collections],
            "collection_count": len(collections), "exhaustive": exhaustive,
            "provider_full_export_supported": True,
            "diagnostics": _diag_dicts(diagnostics)}


def export_collection_timeline(provider, collection_id, *, published_start, published_end, cursor=None,
                               include_text=False, include_notes=True, max_items=500, context=None,
                               registry=None) -> dict:
    """Public archival API, read-only, bounded: enumerate one collection without keywords.

    Records are metadata-level: stable refs, titles, authors, timestamps, type
    labels, counts, and attachment/image descriptors. Body text is included only
    when include_text is set, and is labeled with its actual text scope. Budget
    exhaustion returns the partial items plus a continuation cursor bound to this
    exact provider, collection and window; pass it back as ``cursor`` to resume.
    """
    _require_provider(provider)
    if type(include_text) is not bool or type(include_notes) is not bool:
        raise ValueError("include_text and include_notes must be boolean")
    if type(max_items) is not int or not 1 <= max_items <= _MAX_ITEMS_CAP:
        raise ValueError(f"max_items must be between 1 and {_MAX_ITEMS_CAP}")
    start, end = _window(published_start, published_end)
    context = _context(context)
    scope = _scope(start, end)
    registry = _registry(registry)
    adapter = _adapter(registry, provider, context)
    client = _client(adapter, provider)
    if provider == "zsxq":
        items, scanned, notes, continuation, state, diagnostics = _zsxq_timeline(
            client, collection_id, scope, context=context, start=start, end=end, cursor=cursor,
            include_text=include_text, max_items=max_items)
    elif provider == "ima":
        items, scanned, notes, continuation, state, diagnostics = _ima_timeline(
            client, collection_id, scope, context=context, cursor=cursor, include_text=include_text,
            include_notes=include_notes, max_items=max_items)
    else:
        items, scanned, notes, continuation, state, diagnostics = _wisburg_timeline(
            client, collection_id, scope, context=context, start=start, end=end, cursor=cursor,
            include_text=include_text, max_items=max_items)
    return {"schema_version": "1.0", "provider": provider, "collection_id": collection_id,
            "status": "ok" if state == "complete" else "partial",
            "window": {"start": start.isoformat(), "end": end.isoformat()},
            "items": [item.to_dict() for item in items],
            "continuation_cursor": continuation,
            "coverage": {"state": state, "scanned": scanned, "returned": len(items), "notes": notes},
            "diagnostics": _diag_dicts(diagnostics)}


def _zsxq_group_id(collection_id):
    from ir_search.infrastructure.zsxq import _id as _zsxq_id
    try:
        return _zsxq_id(str(collection_id))
    except (DataAdapterError, TypeError):
        raise ValueError(f"Invalid zsxq collection_id: {collection_id!r}") from None


def _zsxq_directory_row(row):
    """Map one get_user_groups row to a descriptor, tolerating field-name variants.

    The observed official rows carry group_id/name at the top level; nested
    ``group`` envelopes and ``id`` aliases are accepted as fallbacks. Rows whose
    identifier cannot be recovered raise instead of fabricating an id.
    """
    from ir_search.infrastructure.zsxq import _id as _zsxq_id
    if not isinstance(row, dict):
        raise DataAdapterError("upstream_schema")
    source = row.get("group") if isinstance(row.get("group"), dict) else row
    group_id = None
    for candidate in (source.get("group_id"), row.get("group_id"), source.get("id"), row.get("id")):
        if candidate is None or isinstance(candidate, bool):
            continue
        try:
            group_id = _zsxq_id(str(candidate))
            break
        except DataAdapterError:
            continue
    if group_id is None:
        raise DataAdapterError("upstream_schema")
    name = source.get("name", row.get("name"))
    role = source.get("role", row.get("role"))
    return CollectionDescriptor(
        collection_id=group_id,
        name=name if isinstance(name, str) and name.strip() else None,
        role=role if isinstance(role, str) and role.strip() else None)


def _image_entries(value):
    if not value:
        return ()
    if not isinstance(value, list) or len(value) > 50:
        raise DataAdapterError("upstream_schema")
    entries = []
    for index, image in enumerate(value):
        if not isinstance(image, dict):
            continue
        asset_id = image.get("image_id")
        entries.append({"index": index,
                        "image_id": asset_id if _NUMERIC_ID.fullmatch(str(asset_id or "")) else None})
    return tuple(entries)


def _zsxq_item(row, group_id, fetched_at, *, include_text):
    from ir_search.adapters.zsxq_materials import _candidate, _instant
    candidate = _candidate(row, group_id, fetched_at, max_chars=100000)
    labels = row.get("labels")
    if not isinstance(labels, list) or len(labels) > 50 or any(not isinstance(v, str) for v in labels):
        labels = ()
    update_time = row.get("update_time")
    try:
        updated_at = _instant(update_time) if update_time else None
    except DataAdapterError:
        updated_at = None
    authors = tuple(candidate.authors)
    return TimelineItem(
        ref=candidate.source_ref, title=candidate.title, type=candidate.source_record_type or "talk",
        collection_id=group_id, published_at=candidate.published_at, updated_at=updated_at,
        author=authors[0] if len(authors) == 1 else None, authors=authors, labels=tuple(labels),
        like_count=_int_or_none(row.get("like_count")),
        comments_count=_int_or_none(row.get("comments_count")),
        attachments=tuple(a.to_dict() for a in candidate.attachments),
        images=_image_entries(row.get("images")),
        text=candidate.text if include_text else None,
        text_scope="source_excerpt" if include_text and candidate.text else None,
        original_url=candidate.original_url, warnings=candidate.warnings)


def _zsxq_timeline(client, collection_id, scope, *, context, start, end, cursor, include_text, max_items):
    from ir_search.adapters.zsxq_materials import _timeline_advance, _timeline_page
    from ir_search.infrastructure.zsxq import _time as _zsxq_time
    group_id = _zsxq_group_id(collection_id)
    state = _resume(cursor, "zsxq", group_id, scope, context)
    upper = datetime.combine(end + timedelta(days=1), datetime.min.time(), _CST)
    start_cursor = (upper - timedelta(microseconds=1000)).isoformat(timespec="milliseconds")
    walk_cursor = state["cursor"] if state else start_cursor
    if _zsxq_time(walk_cursor) > _zsxq_time(start_cursor):
        raise DataAdapterError("invalid_cursor")
    seen = set(state["seen"]) if state else set()
    diagnostics = []
    items = []
    scanned = 0
    continuation = None
    coverage = "complete"
    for _ in range(_page_budget(max_items, 30)):
        context.check_active()
        remaining = max_items - len(items)
        if remaining <= 0:
            coverage = "budget_exhausted"
            break
        count = min(30, remaining)
        page_size = state["page_size"] if state and state["offset"] else count
        if not 1 <= page_size <= 30:
            raise DataAdapterError("invalid_cursor")
        try:
            response = client.read("get_group_topics",
                                   {"group_id": group_id, "limit": page_size, "scope": "all", "end_time": walk_cursor},
                                   context=context)
            rows, more, next_cursor = _timeline_page(response.data)
        except (DataAdapterError, RequestStopped) as exc:
            diagnostics.append(_diag(exc.code, "zsxq", _stop_failure(exc)))
            if isinstance(exc, RequestStopped) or _stop_source(exc.code):
                return (items, scanned, 0, continuation, _stopped_coverage(exc), diagnostics)
            coverage = "upstream_stopped"
            break
        selected, position = _slice(rows, count, state)
        state = None
        advanced, stop_code = _timeline_advance(walk_cursor, more, next_cursor, window_start=start)
        if stop_code:
            diagnostics.append(_diag(stop_code, "zsxq"))
        continuation = _continuation(scope, "zsxq", group_id, "", walk_cursor, rows, position,
                                     next_cursor if advanced else "", more, context, page_size=page_size,
                                     seen=[str(r.get("topic_id")) for r in rows[:position]
                                           if _NUMERIC_ID.fullmatch(str(r.get("topic_id", "")))])
        scanned += len(selected)
        for row in selected:
            topic_id = str(row.get("topic_id", ""))
            if topic_id in seen:
                diagnostics.append(_diag("duplicate_topic_boundary", "zsxq"))
                continue
            seen.add(topic_id)
            try:
                item = _zsxq_item(row, group_id, response.fetched_at, include_text=include_text)
            except (DataAdapterError, ValueError, TypeError):
                diagnostics.append(_diag("invalid_community_record", "zsxq"))
                continue
            published_day = item.published_at.astimezone(_CST).date() if item.published_at else None
            if published_day and not start <= published_day <= end:
                diagnostics.append(_diag("community_outside_publication_window", "zsxq"))
                continue
            items.append(item)
        if position < len(rows):
            coverage = "budget_exhausted"
            break
        if advanced is None:
            break
        walk_cursor = advanced
    else:
        coverage = "budget_exhausted"
        diagnostics.append(_diag("community_page_budget_exhausted", "zsxq", FailureKind.BUDGET_EXHAUSTED))
    return items, scanned, 0, continuation, coverage, diagnostics


def _ima_collection_id(collection_id):
    from ir_search.infrastructure.ima import _id as _ima_id
    try:
        return _ima_id(str(collection_id))
    except (DataAdapterError, TypeError):
        raise ValueError(f"Invalid ima collection_id: {collection_id!r}") from None


def _ima_item(candidate, row, *, include_text):
    media_type = row.get("media_type")
    type_label = _IMA_MEDIA_TYPE_LABELS.get(media_type, "unknown") if type(media_type) is int else "unknown"
    return TimelineItem(
        ref=candidate.source_ref, title=candidate.title, type=type_label,
        collection_id=candidate.collection_id or "",
        created_at=candidate.source_created_at, modified_at=candidate.source_updated_at,
        text=candidate.text if include_text else None,
        text_scope=candidate.text_scope.value if include_text and candidate.text else None,
        warnings=candidate.warnings)


def _ima_note_item(candidate, *, include_text):
    return TimelineItem(
        ref=candidate.source_ref, title=candidate.title, type="note", collection_id="notes",
        created_at=candidate.source_created_at, modified_at=candidate.source_updated_at,
        text=candidate.text if include_text else None,
        text_scope=candidate.text_scope.value if include_text and candidate.text else None,
        warnings=candidate.warnings)


def _ima_timeline(client, collection_id, scope, *, context, cursor, include_text, include_notes, max_items):
    from ir_search.adapters.ima_materials import IMAMaterialAdapter
    from ir_search.infrastructure.ima import _page
    kid = _ima_collection_id(collection_id)
    media_state, notes_state = None, None
    if cursor is not None:
        state = _decode(cursor)
        if state["provider"] != "ima" or state["collection"] not in {kid, "notes"}:
            raise DataAdapterError("invalid_cursor")
        if state["binding"] != _fingerprint(scope, context.account_scope):
            raise DataAdapterError("invalid_cursor")
        media_state = state if state["collection"] == kid else None
        notes_state = state if state["collection"] == "notes" else None
    diagnostics = []
    items = []
    scanned = 0
    notes = 0
    continuation = None
    coverage = "complete"
    diagnostics.append(_diag("ima_publication_dates_not_provided", "ima"))
    if notes_state is None:
        walk = media_state["cursor"] if media_state else ""
        visited = set()
        state = media_state
        for _ in range(_page_budget(max_items, 20)):
            context.check_active()
            remaining = max_items - len(items)
            if remaining <= 0:
                coverage = "budget_exhausted"
                break
            try:
                reply = client.read("search_knowledge",
                                    {"query": "", "knowledge_base_id": kid, "cursor": walk}, context=context)
                rows, more, next_cursor = _page(reply.data, "info_list")
            except (DataAdapterError, RequestStopped) as exc:
                diagnostics.append(_diag(exc.code, "ima", _stop_failure(exc)))
                if isinstance(exc, RequestStopped) or _stop_source(exc.code):
                    return (items, scanned, notes, continuation, _stopped_coverage(exc), diagnostics)
                coverage = "upstream_stopped"
                break
            selected, position = _slice(rows, remaining, state)
            state = None
            advance = more is True and next_cursor and next_cursor != walk and next_cursor not in visited
            continuation = _continuation(scope, "ima", kid, "", walk, rows, position,
                                         next_cursor if advance else "", more, context)
            scanned += len(selected)
            for row in selected:
                try:
                    candidate = IMAMaterialAdapter._candidate(row, "media", kid, None, reply.fetched_at, 100000)
                except (DataAdapterError, ValueError, TypeError):
                    diagnostics.append(_diag("invalid_ima_record", "ima", FailureKind.UPSTREAM_SCHEMA))
                    continue
                if candidate is None:
                    diagnostics.append(_diag("ima_non_document_search_hit", "ima"))
                    continue
                items.append(_ima_item(candidate, row, include_text=include_text))
            if position < len(rows):
                coverage = "budget_exhausted"
                break
            if more is False:
                break
            if more is None:
                diagnostics.append(_diag("ima_pagination_unknown", "ima"))
                break
            if not rows or not advance:
                diagnostics.append(_diag("ima_pagination_stalled", "ima", FailureKind.UPSTREAM_SCHEMA))
                break
            visited.add(next_cursor)
            walk = next_cursor
        else:
            coverage = "budget_exhausted"
            diagnostics.append(_diag("ima_page_budget_exhausted", "ima", FailureKind.BUDGET_EXHAUSTED))
    if include_notes and coverage == "complete":
        notes_coverage, notes_continuation, notes_scanned, notes_returned = _ima_notes(
            client, scope, context=context, state=notes_state, include_text=include_text,
            max_items=max_items, items=items, diagnostics=diagnostics)
        scanned += notes_scanned
        notes = notes_returned
        if notes_continuation is not None:
            continuation = notes_continuation
        if notes_coverage != "complete":
            coverage = notes_coverage
    return items, scanned, notes, continuation, coverage, diagnostics


def _ima_notes(client, scope, *, context, state, include_text, max_items, items, diagnostics):
    """Enumerate personal notes by offset; returns (coverage, continuation, scanned, notes)."""
    from ir_search.adapters.ima_materials import IMAMaterialAdapter
    from ir_search.infrastructure.ima import _page
    raw_offset = state["cursor"] if state else "0"
    if not raw_offset.isascii() or not raw_offset.isdigit() or not 0 <= int(raw_offset) < 1000:
        raise DataAdapterError("invalid_cursor")
    offset = int(raw_offset or "0")
    continuation = None
    scanned = 0
    returned = 0
    coverage = "complete"
    for _ in range(_page_budget(max_items, 20)):
        context.check_active()
        if len(items) >= max_items:
            coverage = "budget_exhausted"
            break
        parameters = {"search_type": 1, "sort_type": 0, "query_info": {"content": ""},
                      "start": offset, "end": min(1000, offset + 20)}
        try:
            reply = client.read("search_note", parameters, context=context)
            rows, more, _ = _page(reply.data, "search_note_infos")
        except (DataAdapterError, RequestStopped) as exc:
            diagnostics.append(_diag(exc.code, "ima", _stop_failure(exc)))
            if not isinstance(exc, RequestStopped) and exc.code == "unsupported":
                diagnostics.append(_diag("ima_notes_keyword_required", "ima", FailureKind.UNIMPLEMENTED))
                return coverage, continuation, scanned, returned
            if isinstance(exc, RequestStopped) or _stop_source(exc.code):
                return _stopped_coverage(exc), continuation, scanned, returned
            return "upstream_stopped", continuation, scanned, returned
        selected, position = _slice(rows, max_items - len(items), state)
        state = None
        next_position = str(offset + len(rows)) if rows and offset + len(rows) < 1000 else ""
        continuation = _continuation(scope, "ima", "notes", "", str(offset), rows, position,
                                     next_position, more, context)
        scanned += len(selected)
        for row in selected:
            try:
                candidate = IMAMaterialAdapter._candidate(row, "note", None, None, reply.fetched_at, 100000)
            except (DataAdapterError, ValueError, TypeError):
                diagnostics.append(_diag("invalid_ima_record", "ima", FailureKind.UPSTREAM_SCHEMA))
                continue
            items.append(_ima_note_item(candidate, include_text=include_text))
            returned += 1
        if position < len(rows):
            coverage = "budget_exhausted"
            break
        if more is False:
            break
        if more is None:
            diagnostics.append(_diag("ima_pagination_unknown", "ima"))
            break
        if not rows:
            diagnostics.append(_diag("ima_pagination_stalled", "ima", FailureKind.UPSTREAM_SCHEMA))
            break
        offset += len(rows)
        if offset >= 1000:
            diagnostics.append(_diag("ima_note_offset_limit", "ima", FailureKind.BUDGET_EXHAUSTED))
            break
    else:
        diagnostics.append(_diag("ima_page_budget_exhausted", "ima", FailureKind.BUDGET_EXHAUSTED))
        coverage = "budget_exhausted"
    return coverage, continuation, scanned, returned


def _wisburg_item(row, category, fetched_at, *, include_text):
    from ir_search.adapters.wisburg_materials import WisburgMaterialAdapter, _kind
    kind = _kind(category)
    candidate = WisburgMaterialAdapter._candidate(row, category, fetched_at, 100000)
    return TimelineItem(
        ref=candidate.source_ref, title=candidate.title, type=kind, collection_id=category,
        category=category, published_at=row.published_at,
        summary=candidate.text if include_text and kind == "report" and candidate.text else None,
        has_summary=bool(row.text) if kind == "report" else None,
        text=candidate.text if include_text and kind != "report" and candidate.text else None,
        text_scope=candidate.text_scope.value if include_text and kind != "report" and candidate.text else None,
        original_document_available=False if kind == "report" else None,
        warnings=candidate.warnings)


def _wisburg_timeline(client, collection_id, scope, *, context, start, end, cursor, include_text, max_items):
    from ir_search.adapters.wisburg_materials import _CST as _WISBURG_CST
    from ir_search.adapters.wisburg_materials import _kind
    from ir_search.infrastructure.wisburg import _parse_list, _reference
    if not isinstance(collection_id, str) or collection_id not in wisburg_infra.LIST_TOOLS:
        raise ValueError(f"Unknown wisburg collection_id: {collection_id!r}")
    category = collection_id
    state = _resume(cursor, "wisburg", category, scope, context)
    start_arg = start.isoformat() + "T00:00:00+08:00"
    end_arg = (end + timedelta(days=1)).isoformat() + "T00:00:00+08:00"
    after = state["cursor"] if state else None
    seen = set()
    visited = set()
    diagnostics = []
    items = []
    scanned = 0
    continuation = None
    coverage = "complete"
    for _ in range(_page_budget(max_items, 50)):
        context.check_active()
        remaining = max_items - len(items)
        if remaining <= 0:
            coverage = "budget_exhausted"
            break
        first = min(50, remaining)
        arguments = {"first": first, "startTime": start_arg, "endTime": end_arg}
        if after:
            arguments["after"] = after
        try:
            reply = client.read(wisburg_infra.LIST_TOOLS[category], arguments, context=context)
            rows, more, next_cursor = _parse_list(reply.text, category, first=first)
        except (DataAdapterError, RequestStopped) as exc:
            diagnostics.append(_diag(exc.code, "wisburg", _stop_failure(exc)))
            if isinstance(exc, RequestStopped) or _stop_source(exc.code):
                return (items, scanned, 0, continuation, _stopped_coverage(exc), diagnostics)
            coverage = "upstream_stopped"
            break
        selected, position = _slice(rows, remaining, state)
        state = None
        advance = more is True and next_cursor and next_cursor != after and next_cursor not in visited
        continuation = _continuation(scope, "wisburg", category, "", after or "", rows, position,
                                     next_cursor if advance else "", more, context)
        scanned += len(selected)
        for row in selected:
            published_day = row.published_at.astimezone(_WISBURG_CST).date()
            if not start <= published_day <= end:
                diagnostics.append(_diag("wisburg_outside_publication_window", "wisburg"))
                continue
            reference = _reference(_kind(category), row.identifier)
            if reference in seen:
                diagnostics.append(_diag("wisburg_duplicate_report_id", "wisburg"))
                continue
            seen.add(reference)
            items.append(_wisburg_item(row, category, reply.fetched_at, include_text=include_text))
        if position < len(rows):
            coverage = "budget_exhausted"
            break
        if more is False:
            break
        if more is None:
            diagnostics.append(_diag("wisburg_pagination_unknown", "wisburg"))
            break
        if not rows or not advance:
            diagnostics.append(_diag("wisburg_pagination_stalled", "wisburg", FailureKind.UPSTREAM_SCHEMA))
            break
        visited.add(next_cursor)
        after = next_cursor
    else:
        coverage = "budget_exhausted"
        diagnostics.append(_diag("wisburg_page_or_candidate_limit", "wisburg", FailureKind.BUDGET_EXHAUSTED))
    return items, scanned, 0, continuation, coverage, diagnostics


def retrieve_asset(asset_ref, *, max_bytes=52428800, media_policy="all", context=None, registry=None) -> dict:
    """Public archival API, read-only, bounded: download original attachment bytes.

    Returns a dict whose ``content`` field holds the raw bytes object (the caller
    persists it); every other field is JSON-serializable. Failures return
    ``status`` ``failed`` or ``metadata_only`` with a typed ``reason`` instead of
    raising; wisburg originals do not exist upstream and return ``unsupported``.
    ``max_bytes`` is a required positive per-call budget with no library-defined
    upper ceiling; callers remain responsible for memory and disk capacity.
    """
    if not isinstance(asset_ref, str) or not asset_ref.strip():
        raise ValueError("asset_ref must be a nonempty string")
    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError("max_bytes must be a positive integer")
    if media_policy not in {"all", "text_non_audio"}:
        raise ValueError("invalid_media_policy")
    context = _context(context)
    if asset_ref.startswith("wisburg://"):
        return _asset_result("unsupported", ref=asset_ref,
                             reason="upstream_does_not_provide_original_files")
    registry = _registry(registry)
    if asset_ref.startswith("zsxq://"):
        return _zsxq_asset(registry, asset_ref, max_bytes=max_bytes, context=context, media_policy=media_policy)
    if asset_ref.startswith("ima://"):
        return _ima_asset(registry, asset_ref, max_bytes=max_bytes, context=context, media_policy=media_policy)
    return _asset_result("failed", ref=asset_ref, reason="unsupported_asset_ref")


def _asset_result(status, *, ref, reason=None, filename=None, media_type=None, size_bytes=None,
                  sha256=None, warnings=(), content=None):
    return {"status": status, "ref": ref, "reason": reason, "filename": filename,
            "media_type": media_type, "size_bytes": size_bytes, "content_sha256": sha256,
            "content": content, "warnings": list(warnings)}


def _zsxq_media_label(media_type):
    return "application/pdf" if media_type == "pdf" else "application/octet-stream"


def _non_audio_asset(filename, media_type):
    from pathlib import PurePath
    suffix = PurePath(filename or '').suffix.lower()
    if suffix in {'.mp3', '.m4a', '.wav', '.aac', '.flac', '.ogg', '.opus', '.wma', '.amr', '.mp4', '.mov', '.mkv', '.avi', '.webm'}:
        return False
    return suffix in {'.pdf', '.doc', '.docx', '.ppt', '.pptx', '.xls', '.xlsx', '.txt', '.html', '.md', '.rtf', '.csv', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'} or media_type in {'pdf', 'docx', 'pptx', 'xlsx', 'txt', 'html', 'image'}


def _zsxq_asset(registry, asset_ref, *, max_bytes, context, media_policy="all"):
    from ir_search.adapters.zsxq_materials import _candidate
    from ir_search.infrastructure.public_web import _url
    from ir_search.infrastructure.zsxq import _error_code
    try:
        mode, group_id, topic_id, file_id = zsxq_documents._reference(asset_ref)
    except DataAdapterError:
        return _asset_result("failed", ref=asset_ref, reason="invalid_asset_ref")
    if mode != "file":
        return _asset_result("failed", ref=asset_ref, reason="not_a_file_ref")
    adapter = _adapter(registry, "zsxq", context)
    client = _client(adapter, "zsxq")
    try:
        response = client.read("get_topic_info", {"topic_id": topic_id}, context=context)
        topic = response.data.get("topic")
        if not isinstance(topic, dict):
            raise DataAdapterError("upstream_schema")
        candidate = _candidate(topic, group_id, response.fetched_at, max_chars=100000, full=True)
        attachment = next((a for a in candidate.attachments if a.source_ref == asset_ref), None)
        if attachment is None:
            return _asset_result("failed", ref=asset_ref, reason="attachment_not_found_in_topic",
                                 filename=None, media_type=None, size_bytes=None)
    except RequestStopped:
        raise
    except (DataAdapterError, ValueError, TypeError) as exc:
        code = exc.code if isinstance(exc, DataAdapterError) else "upstream_schema"
        return _asset_result("failed", ref=asset_ref, reason=code)
    metadata = {"filename": attachment.name, "media_type": _zsxq_media_label(attachment.media_type)}
    if media_policy == "text_non_audio" and not _non_audio_asset(attachment.name, attachment.media_type):
        return _asset_result("metadata_only", ref=asset_ref, reason="media_excluded_by_policy", **metadata)
    if attachment.size_bytes is not None and attachment.size_bytes > max_bytes:
        return _asset_result("failed", ref=asset_ref, reason="asset_exceeds_max_bytes",
                             size_bytes=attachment.size_bytes, **metadata)
    try:
        reply = client.read("call_zsxq_api", {"method": "GET", "path": f"/v2/files/{file_id}/download_url"},
                            context=context)
        body = reply.data.get("body")
        if reply.data.get("status_code") != 200 or not isinstance(body, dict) or body.get("succeeded") is not True:
            raise DataAdapterError(_error_code(message=body))
        data = body.get("resp_data")
        if not isinstance(data, dict):
            raise DataAdapterError("upstream_schema")
        download_url = data.get("download_url")
        _, host, _ = _url(download_url, signed_download=True)
        if host != _ZSXQ_DOWNLOAD_HOST:
            raise DataAdapterError("blocked_url")
        raw = zsxq_documents._download(
            download_url, context=context, require_pdf=False, max_bytes=max_bytes)
        context.check_active()
        if not isinstance(raw, bytes) or len(raw) > max_bytes:
            return _asset_result("failed", ref=asset_ref, reason="asset_exceeds_max_bytes",
                                 size_bytes=attachment.size_bytes, **metadata)
        warnings = []
        if attachment.media_type == "pdf":
            warnings.append("pdf_magic_verified" if raw[:5] == b"%PDF-" else "pdf_magic_mismatch")
        if attachment.size_bytes is not None and attachment.size_bytes != len(raw):
            warnings.append("declared_size_mismatch")
        return _asset_result("ok", ref=asset_ref, sha256=sha256(raw).hexdigest(), size_bytes=len(raw),
                             warnings=warnings, content=raw, **metadata)
    except RequestStopped:
        raise
    except DataAdapterError as exc:
        return _asset_result("metadata_only", ref=asset_ref, reason=exc.code,
                             size_bytes=attachment.size_bytes, **metadata)


def _ima_asset(registry, asset_ref, *, max_bytes, context, media_policy="all"):
    from ir_search.infrastructure.ima import _parse_ref, error_diagnostics
    try:
        kind, identifier = _parse_ref(asset_ref)
    except DataAdapterError:
        return _asset_result("failed", ref=asset_ref, reason="invalid_asset_ref")
    if kind != "media":
        return _asset_result("failed", ref=asset_ref, reason="notes_do_not_provide_file_bytes")
    adapter = _adapter(registry, "ima", context)
    client = _client(adapter, "ima")
    metadata = {"filename": None, "media_type": None}
    media_type = None
    try:
        reply = client.read("get_media_info", {"media_id": identifier}, context=context)
        data = reply.data
        media_type = data.get("media_type")
        if type(media_type) is not int:
            raise DataAdapterError("upstream_schema")
        filename = data.get("file_name") or data.get("title")
        metadata["filename"] = filename if isinstance(filename, str) and filename.strip() else None
        metadata["media_type"] = _IMA_MEDIA_LABELS.get(_IMA_MEDIA_TYPE_LABELS.get(media_type, ""), "application/octet-stream")
        if media_policy == "text_non_audio" and not _non_audio_asset(metadata["filename"], _IMA_MEDIA_TYPE_LABELS.get(media_type)):
            return _asset_result("metadata_only", ref=asset_ref, reason="media_excluded_by_policy", **metadata)
        if media_type == 11:
            return _asset_result("failed", ref=asset_ref, reason="notes_do_not_provide_file_bytes", **metadata)
        if media_type in (2, 6):
            return _asset_result("failed", ref=asset_ref,
                                 reason="public_web_original_not_exported_as_bytes", **metadata)
        if media_type not in _IMA_DOWNLOAD_MEDIA_TYPES:
            return _asset_result("failed", ref=asset_ref, reason="ima_media_type_not_exportable", **metadata)
        info = data.get("url_info")
        if not isinstance(info, dict) or not info.get("url"):
            return _asset_result("failed", ref=asset_ref, reason="ima_original_unavailable", **metadata)
        url = info["url"]
        headers = info.get("headers") or {}
        if not isinstance(headers, dict):
            raise DataAdapterError("upstream_schema")
    except RequestStopped:
        raise
    except DataAdapterError as exc:
        result = _asset_result("failed", ref=asset_ref, reason=exc.code, **metadata)
        diagnostic = error_diagnostics(exc)
        if diagnostic: result['diagnostics'] = diagnostic
        return result
    try:
        download = ima_documents._download(url, headers, context=context, max_bytes=max_bytes)
        raw = download.body
        if not isinstance(raw, bytes) or len(raw) > max_bytes:
            return _asset_result("failed", ref=asset_ref, reason="asset_exceeds_max_bytes", **metadata)
        if media_type == 1 and raw[:5] != b"%PDF-":
            return _asset_result("failed", ref=asset_ref, reason="web_content_unsupported", **metadata)
        return _asset_result("ok", ref=asset_ref, sha256=sha256(raw).hexdigest(), size_bytes=len(raw),
                             warnings=[], content=raw, **metadata)
    except RequestStopped:
        raise
    except DataAdapterError as exc:
        return _asset_result("metadata_only", ref=asset_ref, reason=exc.code, **metadata)


def list_topic_comments(provider, ref, *, cursor=None, per_page=30, max_pages=10, context=None,
                        registry=None) -> dict:
    """Public archival API, read-only, bounded: enumerate comments of one zsxq topic.

    Only zsxq is supported; other providers return ``status`` ``not_supported``.
    Pages walk the official index cursor until the topic is exhausted, max_pages is
    reached or the operation budget stops the request; a continuation cursor bound
    to this topic resumes the walk later. Nested replies are preserved when the
    upstream response embeds them.
    """
    _require_provider(provider)
    if type(per_page) is not int or not 1 <= per_page <= 30:
        raise ValueError("per_page must be between 1 and 30")
    if type(max_pages) is not int or not 1 <= max_pages <= _MAX_COMMENT_PAGES:
        raise ValueError(f"max_pages must be between 1 and {_MAX_COMMENT_PAGES}")
    if provider != "zsxq":
        return {"schema_version": "1.0", "provider": provider, "ref": ref, "status": "not_supported",
                "comments": [], "continuation_cursor": None,
                "coverage": {"state": "not_supported", "scanned": 0, "returned": 0},
                "diagnostics": _diag_dicts([_diag("comments_not_supported_for_provider", provider,
                                                  FailureKind.UNIMPLEMENTED)])}
    try:
        mode, group_id, topic_id, _ = zsxq_documents._reference(ref)
    except DataAdapterError:
        raise ValueError(f"Invalid zsxq topic ref: {ref!r}") from None
    if mode != "topic":
        raise ValueError(f"Invalid zsxq topic ref: {ref!r}")
    context = _context(context)
    scope = _ExportScope(f"comments:{topic_id}", (), (), (), None, None)
    registry = _registry(registry)
    adapter = _adapter(registry, "zsxq", context)
    client = _client(adapter, "zsxq")
    state = _resume(cursor, "zsxq", topic_id, scope, context)
    index = state["cursor"] if state else None
    diagnostics = []
    comments = []
    scanned = 0
    continuation = None
    coverage = "complete"
    nested_seen = False
    for _ in range(max_pages):
        context.check_active()
        arguments = {"topic_id": topic_id, "limit": per_page}
        if index:
            arguments["index"] = index
        try:
            response = client.read("get_topic_comments", arguments, context=context)
            data = response.data
            rows = data.get("comments")
            if not isinstance(rows, list) or len(rows) > per_page or any(not isinstance(r, dict) for r in rows):
                raise DataAdapterError("upstream_schema")
            more_raw = data.get("has_more")
            more = more_raw if type(more_raw) is bool else None
            next_index = data.get("index")
            if not isinstance(next_index, str) or not re.fullmatch(r"[A-Za-z0-9_.:+/=-]{1,512}", next_index):
                next_index = None
        except (DataAdapterError, RequestStopped) as exc:
            diagnostics.append(_diag(exc.code, "zsxq", _stop_failure(exc)))
            if isinstance(exc, RequestStopped) or _stop_source(exc.code):
                coverage = _stopped_coverage(exc)
            else:
                coverage = "upstream_stopped"
            break
        scanned += len(rows)
        continuation = _continuation(scope, "zsxq", topic_id, "", index or "", rows, len(rows),
                                     next_index or "", more, context)
        for row in rows:
            if "replies" in row or "children" in row:
                nested_seen = True
            try:
                comments.append(_comment_item(row, group_id, topic_id))
            except (DataAdapterError, ValueError, TypeError):
                diagnostics.append(_diag("invalid_comment_record", "zsxq"))
        if more is False:
            break
        if more is None:
            diagnostics.append(_diag("comments_pagination_unknown", "zsxq"))
            break
        if not next_index:
            diagnostics.append(_diag("comments_index_not_provided", "zsxq"))
            break
        index = next_index
    else:
        coverage = "budget_exhausted"
        diagnostics.append(_diag("comment_page_budget_exhausted", "zsxq", FailureKind.BUDGET_EXHAUSTED))
    if not nested_seen:
        diagnostics.append(_diag("nested_replies_not_provided", "zsxq"))
    return {"schema_version": "1.0", "provider": provider, "ref": ref,
            "status": "ok" if coverage == "complete" else "partial",
            "comments": [comment.to_dict() for comment in comments],
            "continuation_cursor": continuation,
            "coverage": {"state": coverage, "scanned": scanned, "returned": len(comments)},
            "diagnostics": _diag_dicts(diagnostics)}


def _reply_target(value):
    if not isinstance(value, dict):
        return None
    target = {}
    comment_id = value.get("comment_id")
    if isinstance(comment_id, (str, int)) and not isinstance(comment_id, bool):
        target["comment_id"] = str(comment_id)
    owner = value.get("owner")
    if isinstance(owner, dict) and isinstance(owner.get("name"), str):
        target["author"] = owner["name"]
    return target or None


def _comment_item(row, group_id, topic_id, *, depth=0):
    from ir_search.adapters.zsxq_materials import _id as _zsxq_id, _instant, _name
    comment_id = _zsxq_id(str(row.get("comment_id", "")))
    text = row.get("text", row.get("content", ""))
    if not isinstance(text, str):
        raise DataAdapterError("upstream_schema")
    owner = row.get("owner", row.get("author"))
    author = _name(owner) or None
    role = owner.get("role") if isinstance(owner, dict) and isinstance(owner.get("role"), str) else None
    replies = ()
    warnings = []
    nested = row.get("replies") if "replies" in row else row.get("children")
    if isinstance(nested, list) and depth < _MAX_COMMENT_DEPTH:
        replies = tuple(_comment_item(child, group_id, topic_id, depth=depth + 1)
                        for child in nested[:50] if isinstance(child, dict))
        if len(nested) > 50:
            warnings.append("nested_replies_capped")
    elif isinstance(nested, list):
        warnings.append("nested_replies_depth_capped")
    return TimelineComment(
        ref=f"zsxq://comment/{group_id}/{topic_id}/{comment_id}", comment_id=comment_id, text=text,
        author=author, role=role, created_at=_instant(row.get("create_time")),
        reply_to=_reply_target(row.get("reply_to")), replies=replies,
        like_count=_int_or_none(row.get("like_count")), warnings=warnings)
