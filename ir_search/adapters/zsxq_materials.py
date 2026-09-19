"""Bounded community timelines and attributed original text for research skills."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
from urllib.parse import unquote
import re

from ir_search.contracts import AdapterMode, Diagnostic, Provenance
from ir_search.contracts.materials import (MaterialAttachment, MaterialCandidate, MaterialCapability,
    MaterialKind, MaterialSearchPage, MaterialSection, MaterialSourceScan, TextScope)
from ir_search.infrastructure.credentials import ZsxqProfile
from ir_search.infrastructure.zsxq import ZsxqClient, _id, _time
from ir_search.models import EvidenceType, SourceAuthority, SourceTier
from ir_search.registry import DataAdapterError
from ir_search.infrastructure.material_cursor import _states, _slice, _continuation
from ir_search.infrastructure.recovery import _stop_source
from ir_search.context import RequestStopped

_KINDS = (MaterialKind.SOCIAL_POST, MaterialKind.QA)
_CST = timezone(timedelta(hours=8))


def _diag(code, error=None):
    return Diagnostic(code, "search_materials", "zsxq", adapter_mode=AdapterMode.LIVE,
                      **({"failure_kind": error.failure_kind} if error else {}))


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}: self.skip += 1
        if self.skip: return
        if tag in {"p", "div", "br", "li"}: self.parts.append("\n")
        if tag == "e":
            value = dict(attrs).get("title")
            if value: self.parts.append(unquote(value))

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"} and self.skip: self.skip -= 1
        if not self.skip and tag in {"p", "div", "li"}: self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip: self.parts.append(data)


def _plain(value):
    if not isinstance(value, str): raise DataAdapterError("upstream_schema")
    parser = _Text()
    parser.feed(value)
    return "\n".join(line.strip() for line in unescape("".join(parser.parts)).splitlines() if line.strip())


def _name(owner):
    if owner is None: return ""
    if not isinstance(owner, dict) or not isinstance(owner.get("name", ""), str): raise DataAdapterError("upstream_schema")
    return owner.get("name", "")


def _instant(value):
    if not value: return None
    return _time(value).astimezone(_CST)


def _topic_ref(group, topic):
    return f"zsxq://topic/{_id(group)}/{_id(topic)}"


def _candidate(topic, group_id, fetched_at, *, max_chars, full=False, comments=()):
    if not isinstance(topic, dict): raise DataAdapterError("upstream_schema")
    tid = _id(str(topic.get("topic_id", "")))
    group = topic.get("group")
    if not isinstance(group, dict) or str(group.get("group_id")) != group_id:
        raise DataAdapterError("upstream_schema")
    group_name = group.get("name") or group_id
    if not isinstance(group_name, str): raise DataAdapterError("upstream_schema")
    ref = _topic_ref(group_id, tid)
    kind = topic.get("type")
    if kind not in {"talk", "q&a", "task", "solution"}: raise DataAdapterError("unsupported")
    warnings = ["community_content_not_independently_verified", "business_period_unknown"]
    created = _instant(topic.get("create_time"))
    body, sections, authors = "", [], []

    def append(value, role, author, source_ref, timestamp):
        nonlocal body
        text = _plain(value)
        if not text: return
        prefix = "\n\n" if body else ""
        start = len(body) + len(prefix)
        remaining = max_chars - start
        if remaining <= 0:
            warnings.append("text_truncated"); return
        if len(text) > remaining: warnings.append("text_truncated")
        text = text[:remaining]
        body += prefix + text
        sections.append(MaterialSection(role, author, source_ref, start, len(body), timestamp))
        if author: authors.append(author)

    # Brief timeline content is not promised complete by the official endpoint.
    # Only a successful detail read upgrades it to extracted_text.
    question, answer = topic.get("question"), topic.get("answer")
    structured_question = isinstance(question, dict) and isinstance(question.get("text", question.get("content")), str)
    if kind == "q&a" and structured_question:
        append(question.get("text", question.get("content", "")), "question",
               _name(question.get("owner")), ref, _instant(question.get("create_time")) or created)
        if isinstance(answer, dict):
            append(answer.get("text", answer.get("content", "")), "answer", _name(answer.get("owner")),
                   ref, _instant(answer.get("create_time")))
    if kind == "q&a":
        if not structured_question or not isinstance(answer, dict):
            warnings.append("question_answer_roles_unverified")
            full = False
            # The observed question metadata names a questionee, not an answer
            # author. Do not relabel flattened content as that person's answer.
            append(topic.get("content", ""), "unverified", "", ref, created)
    else:
        append(topic.get("content", ""), "post", _name(topic.get("owner")), ref, created)
    for comment in comments:
        if not isinstance(comment, dict): raise DataAdapterError("upstream_schema")
        cid = _id(str(comment.get("comment_id", "")))
        append(comment.get("text", comment.get("content", "")), "comment", _name(comment.get("owner", comment.get("author"))),
               f"zsxq://comment/{group_id}/{tid}/{cid}", _instant(comment.get("create_time")))
    files = topic.get("files") or []
    if not isinstance(files, list) or len(files) > 50: raise DataAdapterError("upstream_schema")
    attachments, seen = [], set()
    for file in files:
        if not isinstance(file, dict): raise DataAdapterError("upstream_schema")
        fid = _id(str(file.get("file_id", "")))
        if fid in seen: continue
        seen.add(fid)
        name = file.get("name")
        if not isinstance(name, str) or not name.strip(): raise DataAdapterError("upstream_schema")
        attachments.append(MaterialAttachment(f"zsxq://file/{group_id}/{tid}/{fid}", name,
            "pdf" if name.lower().endswith(".pdf") or file.get("type") == "application/pdf" else "unknown", file.get("size")))
    if attachments: warnings.append("attachments_metadata_only")
    if topic.get("images"): warnings.append("images_not_read")
    if topic.get("referenced_topic"): warnings.append("referenced_topic_not_read")
    title = _plain(topic.get("title") or "")
    if not title:
        title = body.splitlines()[0][:160] if body else attachments[0].name if attachments else "主题 " + tid
        warnings.append("title_derived_from_content_or_identifier")
    if not full and body: warnings.append("timeline_text_completeness_unverified")
    return MaterialCandidate(ref, title, MaterialKind.QA if kind == "q&a" else MaterialKind.SOCIAL_POST, "community",
        Provenance("zsxq", group_name, fetched_at, source_tier=SourceTier.UGC, authority=SourceAuthority.UGC,
                   evidence_type=EvidenceType.SOCIAL_POST, adapter_mode=AdapterMode.LIVE),
        text=body, text_scope=TextScope.EXTRACTED_TEXT if body and full else TextScope.SOURCE_EXCERPT if body else TextScope.METADATA,
        original_url=f"https://wx.zsxq.com/group/{group_id}/topic/{tid}", published_on=created.date() if created else None,
        published_at=created, authors=tuple(dict.fromkeys(authors)), source_document_id=tid,
        warnings=tuple(dict.fromkeys(warnings)), collection_id=group_id, collection_name=group_name,
        source_record_type=kind, attachments=tuple(attachments), sections=tuple(sections))


class ZsxqMaterialAdapter:
    name = "zsxq"

    def __init__(self, profile: ZsxqProfile, *, client_factory=None):
        if not isinstance(profile, ZsxqProfile): raise ValueError("ZsxqProfile required")
        self._profile, self._client_factory = profile, client_factory or ZsxqClient
        self.capability = MaterialCapability(self.name, "community", _KINDS, supports_publication_filter=False,
            search_basis="bounded_community_timeline_local_matching", coverage_notes=(
                "Official MCP timeline only; no RAG, browser credentials, CLI or generated summaries",
                "Configured groups in order, finite pages/candidates; dates filtered in Asia/Shanghai",
                "Timeline snippets and detail text distinguished; comments optional and attributed",
                "Attachments are metadata in search; explicit file references can be retrieved separately",
                "Directory pages, time cursors and unscanned collections never imply complete coverage",))

    def search_materials(self, request, *, context):
        """Read bounded time pages, deduplicate IDs and fetch attributed details by budget."""
        if not request.published_start or context.account_scope != self.capability.account_scope:
            raise DataAdapterError("unsupported")
        client = self._client_factory(self._profile)
        page = MaterialSearchPage([], 0, diagnostics=[_diag("bounded_community_timeline"),
            _diag("community_publication_filter_local")])
        states = _states(request, self.name, context)
        groups = list(request.zsxq_group_ids or self._profile.group_ids)
        if request.zsxq_group_ids and self._profile.group_ids and not set(groups) <= set(self._profile.group_ids):
            raise DataAdapterError('entitlement_denied')
        if states:
            resumed = [key[0] for key in states]
            if any(key[1] != '' for key in states) or (groups and not set(resumed) <= set(groups)):
                raise DataAdapterError('invalid_cursor')
            groups = resumed
        if not groups:
            directory = client.groups(context=context)
            groups = list(dict.fromkeys(_id(str(g.get("group_id", ""))) for g in directory.data["groups"]))
            page.diagnostics.append(_diag("collection_directory_not_exhaustive"))
        if len(groups) > self._profile.max_groups: page.diagnostics.append(_diag("collection_budget_exhausted"))
        groups = groups[:self._profile.max_groups]
        if not groups: page.diagnostics.append(_diag("no_accessible_collections")); return page
        # Share the finite candidate budget so one collection cannot consume it all.
        shares = [request.candidates_per_source // len(groups) + (i < request.candidates_per_source % len(groups)) for i in range(len(groups))]
        seen = set()
        upper = datetime.combine(request.published_end + timedelta(days=1), datetime.min.time(), _CST)
        start_cursor = (upper - timedelta(microseconds=1000)).isoformat(timespec="milliseconds")
        for group_index, (gid, share) in enumerate(zip(groups, shares)):
            reads = 0
            read_limit = request.text_reads_per_source // len(groups) + (group_index < request.text_reads_per_source % len(groups))
            if not share:
                page.diagnostics.append(_diag("candidate_budget_exhausted")); continue
            state = states.get((gid,''))
            cursor, consumed = state['cursor'] if state else start_cursor, 0
            if _time(cursor) > _time(start_cursor): raise DataAdapterError('invalid_cursor')
            if state: seen.update((gid, tid) for tid in state['seen'])
            continuation = None
            for _ in range(self._profile.max_pages_per_group):
                context.check_active()
                count = min(30, share - consumed)
                if count <= 0: break
                page_size = state['page_size'] if state and state['offset'] else count
                if not 1 <= page_size <= 30: raise DataAdapterError('invalid_cursor')
                try:
                    response = client.read("get_group_topics", {"group_id": gid, "limit": page_size, "scope": "all", "end_time": cursor}, context=context)
                    data = response.data
                    rows, more, next_cursor = data.get("topics_brief"), data.get("has_more"), data.get("next_end_time")
                    if (not isinstance(rows, list) or len(rows) > 1000 or any(not isinstance(r, dict) for r in rows)
                            or type(more) is not bool or not isinstance(next_cursor, str)):
                        raise DataAdapterError("upstream_schema")
                    if next_cursor: _time(next_cursor)
                    selected, position = _slice(rows, count, state)
                    # Re-fetch the same bounded page shape when resuming a clipped upstream response.
                    advancing = (next_cursor and _time(next_cursor) < _time(cursor)
                                 and _time(next_cursor).astimezone(_CST).date() >= request.published_start)
                    continuation = _continuation(request, self.name, gid, '', cursor, rows, position,
                        next_cursor if advancing else '', more, context, page_size=page_size,
                        seen=[str(r.get('topic_id')) for r in rows[:position]
                              if re.fullmatch(r'[1-9][0-9]{0,29}',str(r.get('topic_id','')))])
                    state = None
                    page.scans.append(MaterialSourceScan("get_group_topics", MaterialKind.SOCIAL_POST,
                        request.published_start, request.published_end, "queried", len(rows), len(selected),
                        date_filter_basis="local_publication_metadata", collection_id=gid, has_more=more,
                        next_cursor=next_cursor or None))
                    page.scanned_count += len(selected); consumed += len(selected)
                    if len(rows) > count: page.diagnostics.append(_diag("upstream_candidate_limit_exceeded"))
                except (DataAdapterError, RequestStopped) as exc:
                    page.diagnostics.append(_diag(exc.code, exc))
                    page.scans.append(MaterialSourceScan("get_group_topics", MaterialKind.SOCIAL_POST,
                        request.published_start, request.published_end, exc.code,
                        date_filter_basis="local_publication_metadata", collection_id=gid))
                    if isinstance(exc, RequestStopped) or _stop_source(exc.code):
                        if continuation: page.continuation_cursors.append(continuation)
                        return page
                    break
                stop_code = None
                for row_index, row in enumerate(selected):
                    context.check_active()
                    try:
                        tid = _id(str(row.get("topic_id", "")))
                        if (gid, tid) in seen:
                            page.diagnostics.append(_diag("duplicate_topic_boundary")); continue
                        seen.add((gid, tid))
                        candidate = _candidate(row, gid, response.fetched_at, max_chars=request.max_chars)
                        if candidate.published_on and not request.published_start <= candidate.published_on <= request.published_end:
                            page.diagnostics.append(_diag("community_outside_publication_window")); continue
                        if reads < read_limit:
                            reads += 1
                            try:
                                detail = client.read("get_topic_info", {"topic_id": tid}, context=context)
                                topic = detail.data.get("topic")
                                if not isinstance(topic, dict) or str(topic.get("topic_id")) != tid: raise DataAdapterError("upstream_schema")
                                candidate = _candidate(topic, gid, detail.fetched_at, max_chars=request.max_chars, full=True)
                                if self._profile.comments_per_topic:
                                    if reads >= read_limit:
                                        candidate = replace(candidate, warnings=candidate.warnings + ("comments_not_read_within_budget",))
                                    else:
                                        reads += 1
                                        try:
                                            comments = client.read("get_topic_comments", {"topic_id": tid, "limit": self._profile.comments_per_topic}, context=context)
                                            comment_rows = comments.data.get("comments")
                                            if not isinstance(comment_rows, list) or len(comment_rows) > self._profile.comments_per_topic: raise DataAdapterError("upstream_schema")
                                            candidate = _candidate(topic, gid, detail.fetched_at, max_chars=request.max_chars, full=True, comments=comment_rows)
                                            candidate = replace(candidate, warnings=candidate.warnings + ("comments_first_page_only",))
                                        except (DataAdapterError, RequestStopped) as exc:
                                            page.diagnostics.append(_diag(exc.code, exc))
                                            candidate = replace(candidate, warnings=candidate.warnings + ("comments_read_failed",))
                                            if isinstance(exc, RequestStopped) or _stop_source(exc.code): stop_code = exc.code
                            except (DataAdapterError, RequestStopped) as exc:
                                page.diagnostics.append(_diag(exc.code, exc))
                                candidate = replace(candidate, warnings=candidate.warnings + ("topic_detail_read_failed",))
                                if isinstance(exc, RequestStopped) or _stop_source(exc.code): stop_code = exc.code
                        else:
                            candidate = replace(candidate, warnings=candidate.warnings + ("text_not_read_within_budget",))
                            page.diagnostics.append(_diag("text_read_budget_exhausted"))
                        if candidate.published_on and not request.published_start <= candidate.published_on <= request.published_end:
                            page.diagnostics.append(_diag("community_outside_publication_window"))
                        else:
                            page.candidates.append(candidate)
                    except (ValueError, TypeError, AttributeError, DataAdapterError):
                        page.diagnostics.append(_diag("invalid_community_record"))
                    if stop_code:
                        remaining = len(selected)-row_index-1
                        position -= remaining; page.scanned_count -= remaining; consumed -= remaining
                        page.scans[-1] = replace(page.scans[-1], inspected_count=len(selected)-remaining)
                        continuation = _continuation(request, self.name, gid, '', cursor, rows, position,
                            next_cursor if advancing else '', more, context, page_size=page_size,
                            seen=[str(r.get('topic_id')) for r in rows[:position]
                                  if re.fullmatch(r'[1-9][0-9]{0,29}',str(r.get('topic_id','')))])
                        if continuation: page.continuation_cursors.append(continuation)
                        return page
                if position < len(rows): break
                if not more: break
                if not next_cursor or _time(next_cursor) >= _time(cursor):
                    page.diagnostics.append(_diag("community_cursor_not_advancing")); break
                if _time(next_cursor).astimezone(_CST).date() < request.published_start: break
                cursor = next_cursor
            else:
                page.diagnostics.append(_diag("community_page_budget_exhausted"))
            if continuation: page.continuation_cursors.append(continuation)
            if consumed >= share: page.diagnostics.append(_diag("candidate_budget_exhausted"))
        page.diagnostics = list(dict.fromkeys(page.diagnostics))
        return page
