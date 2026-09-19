"""Typed announcement discovery separate from numeric rows and LLM answers."""
from __future__ import annotations

import json

from ir_search.adapters.jydb import build_jydb_adapter
from ir_search.context import RequestContext, RequestStopped
from ir_search.contracts import AnnouncementRequest
from ir_search.registry import DataAdapterError


def search_announcements(request: AnnouncementRequest, *, adapter=None, context=None) -> dict:
    """Return one bounded JYDB metadata page with source references for retrieve()."""
    if not isinstance(request, AnnouncementRequest):
        raise ValueError("search_announcements requires AnnouncementRequest")
    context = context if context is not None else RequestContext()
    result = {"schema_version": "1.0", "request_id": context.request_id, "status": "unavailable",
              "items": [], "complete": False, "next_cursor": None, "diagnostics": []}
    try:
        context.begin_operation()
        adapter = adapter if adapter is not None else build_jydb_adapter()
        page = adapter.search_announcements(request, context=context)
        context.check_active()
        json.dumps(page, allow_nan=False)
        result.update(page)
    except RequestStopped as exc:
        result["diagnostics"].append({"code": exc.code, "operation": "search_announcements", "failure_kind": exc.failure_kind.value})
    except DataAdapterError as exc:
        result["diagnostics"].append({"code": exc.code, "operation": "search_announcements", "provider": "jydb", "failure_kind": exc.failure_kind.value})
    except Exception:
        result["status"] = "error"
        result["diagnostics"].append({"code": "invalid_provider_response", "operation": "search_announcements", "provider": "jydb"})
    return result
