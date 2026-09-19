"""JYDB announcement text, using fixed single-table queries and internal source URIs."""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from ir_search.contracts import AnnouncementRequest
from ir_search.documents.models import Document, hash_text, make_doc_id
from ir_search.infrastructure.credentials import mysql_profile, SourceConfigError
from ir_search.infrastructure.mysql import _select
from ir_search.infrastructure.pagination import _decode_cursor, _encode_cursor
from ir_search.models import EvidenceType, SourceTier
from ir_search.registry import DataAdapterError

_CN = ZoneInfo("Asia/Shanghai")
_MARKETS = {"SH": 83, "SZ": 90, "BJ": 18}
_URI = re.compile(r"jydb://announcement/([1-9][0-9]{0,18})")


def _timestamp(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime.combine(value, time())
    if not isinstance(value, datetime):
        raise DataAdapterError("upstream_schema")
    return value.replace(tzinfo=_CN) if value.tzinfo is None else value.astimezone(_CN)


def _positive_int(value):
    if type(value) is not int or not 0 < value < 2 ** 63:
        raise DataAdapterError("upstream_schema")
    return value


class JYDBAnnouncementAdapter:
    name = "jydb"

    def __init__(self, profile, *, select=None):
        if profile.provider != self.name:
            raise ValueError("Wrong provider profile")
        self._profile = profile
        self._select = select or _select

    def search_announcements(self, request: AnnouncementRequest, *, context) -> dict:
        """List LC_Announcement metadata; coverage is this table, not all disclosures."""
        if not isinstance(request, AnnouncementRequest) or context.account_scope != "default":
            raise DataAdapterError("unsupported")
        last = _decode_cursor(request, self._profile)
        conditions, params = [], []
        for symbol in request.symbols:
            code, venue = symbol.split(".")
            conditions.append("(SecuCode = %s AND SecuMarket = %s)")
            params.extend([code, _MARKETS[venue]])
        sql = "SELECT InnerCode, SecuCode, SecuMarket, CompanyCode FROM SecuMain WHERE SecuCategory = 1 AND (" + " OR ".join(conditions) + ") ORDER BY InnerCode LIMIT %s"
        mapped = self._select(self._profile, sql, tuple(params + [201]), max_rows=201, context=context)
        if len(mapped) > 200:
            raise DataAdapterError("upstream_schema")
        companies = set()
        by_symbol = {symbol: set() for symbol in request.symbols}
        for row in mapped:
            venue = next((k for k, v in _MARKETS.items() if v == row.get("SecuMarket")), None)
            symbol = f"{row.get('SecuCode')}.{venue}"
            if symbol not in by_symbol:
                raise DataAdapterError("upstream_schema")
            by_symbol[symbol].add(_positive_int(row.get("CompanyCode")))
        if any(len(ids) != 1 for ids in by_symbol.values()):
            raise DataAdapterError("not_found" if any(not ids for ids in by_symbol.values()) else "upstream_schema")
        for ids in by_symbol.values():
            companies.update(ids)
        conditions = ["CompanyCode IN (" + ",".join(["%s"] * len(companies)) + ")", "InfoPublDate >= %s", "InfoPublDate < %s"]
        start = datetime.combine(request.start, time())
        end = datetime.combine(request.end + timedelta(days=1), time())
        params = sorted(companies) + [start, end]
        if request.query:
            conditions.append("InfoTitle LIKE %s ESCAPE '!'")
            params.append("%" + request.query.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%")
        if last is not None:
            try:
                if not isinstance(last, list) or len(last) != 2:
                    raise ValueError()
                timestamp = _timestamp(datetime.fromisoformat(last[0])).replace(tzinfo=None)
                identity = _positive_int(last[1])
            except Exception:
                raise DataAdapterError("invalid_cursor") from None
            conditions.append("(InfoPublDate, ID) < (%s, %s)")
            params.extend([timestamp, identity])
        sql = "SELECT ID, CompanyCode, InfoPublDate, InfoTitle, Category, Media FROM LC_Announcement WHERE " + " AND ".join(conditions) + " ORDER BY InfoPublDate DESC, ID DESC LIMIT %s"
        rows = self._select(self._profile, sql, tuple(params + [request.limit + 1]), max_rows=request.limit + 1, context=context)
        more, rows = len(rows) > request.limit, rows[:request.limit]
        items, seen = [], set()
        for row in rows:
            identity = _positive_int(row.get("ID"))
            published = _timestamp(row.get("InfoPublDate"))
            if identity in seen or row.get("CompanyCode") not in companies or not request.start <= published.date() <= request.end:
                raise DataAdapterError("upstream_schema")
            seen.add(identity)
            title = row.get("InfoTitle")
            if not isinstance(title, str) or not title.strip():
                raise DataAdapterError("upstream_schema")
            items.append({"source_ref": f"jydb://announcement/{identity}", "reference_type": "provider_record",
                          "title": title, "published_at": published.isoformat(), "provider": self.name,
                          "company_code": row["CompanyCode"], "evidence_type": "announcement",
                          "source_authority": "data_vendor", "source_tier": "COMPANY", "adapter_mode": "live",
                          "category": row.get("Category"), "discovery_channel": row.get("Media")})
        cursor = _encode_cursor(request, self._profile, [items[-1]["published_at"], rows[-1]["ID"]]) if more else None
        return {"schema_version": "1.0", "status": "partial" if more else "ok", "items": items,
                "complete": not more, "next_cursor": cursor, "provider": self.name,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "coverage": {"tables": ["LC_Announcement"], "scope": "table_query_only", "all_disclosures": False},
                "diagnostics": [{"code": "announcement_table_scope", "operation": "search_announcements", "provider": self.name},
                                {"code": "publication_time_precision_unverified", "operation": "search_announcements", "provider": self.name}]}

    def fetch_document(self, source_ref: str, *, context, max_chars=20000) -> Document:
        """Read bounded vendor text; a provider URI is never presented as an official URL."""
        match = _URI.fullmatch(source_ref)
        if not match or int(match.group(1)) >= 2 ** 63 or type(max_chars) is not int or not 1 <= max_chars <= 100000 or context.account_scope != "default":
            raise DataAdapterError("unsupported")
        identity = int(match.group(1))
        sql = "SELECT ID, CompanyCode, InfoPublDate, InfoTitle, LEFT(Content, %s) AS Content FROM LC_Announcement WHERE ID = %s LIMIT %s"
        rows = self._select(self._profile, sql, (max_chars + 1, identity, 2), max_rows=2, context=context)
        if not rows:
            raise DataAdapterError("not_found")
        if len(rows) != 1 or rows[0].get("ID") != identity:
            raise DataAdapterError("upstream_schema")
        row = rows[0]
        text = row.get("Content")
        if not isinstance(text, str) or not text.strip() or not isinstance(row.get("InfoTitle"), str) or not row["InfoTitle"].strip():
            raise DataAdapterError("upstream_schema")
        company = _positive_int(row.get("CompanyCode"))
        publishers = self._select(self._profile, "SELECT DISTINCT ChiName FROM SecuMain WHERE CompanyCode = %s LIMIT %s",
                                  (company, 2), max_rows=2, context=context)
        publisher = publishers[0].get("ChiName") if len(publishers) == 1 else None
        warnings = ["source_uri_not_web_url", "vendor_text_not_original_file", "publication_time_precision_unverified"]
        if not isinstance(publisher, str) or not publisher.strip():
            publisher = "unknown"
            warnings.append("publisher_unknown")
        if len(text) > max_chars:
            warnings.append("text_truncated")
        text = text[:max_chars]
        content_hash = hash_text(text)
        return Document(make_doc_id(source_ref, content_hash), source_ref, source_ref, row["InfoTitle"], self.name,
                        SourceTier.COMPANY, EvidenceType.ANNOUNCEMENT, "text/plain", _timestamp(row["InfoPublDate"]),
                        datetime.now(timezone.utc), "jydb_database_text", text, text_hash=content_hash, warnings=warnings,
                        extra={"adapter_mode": "live", "publisher": publisher, "source_authority": "data_vendor",
                               "reference_type": "provider_record", "table": "LC_Announcement"})


def build_jydb_adapter(*, env_file=None):
    """Construct the enabled client without connecting; missing configuration is explicit."""
    try:
        profile = mysql_profile("jydb", env_file=env_file)
    except SourceConfigError:
        raise DataAdapterError("source_config_error") from None
    if profile is None:
        raise DataAdapterError("source_disabled")
    return JYDBAnnouncementAdapter(profile)
