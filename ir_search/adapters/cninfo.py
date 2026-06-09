from __future__ import annotations

import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from ir_search.adapters.base import AdapterError
from ir_search.models import EntityType, EvidenceType, Hit, Query, SourceTier
from ir_search.network import http_error_message, open_url


class CninfoAdapter:
    name = "cninfo"
    mode = "live"
    endpoint = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    base_url = "https://static.cninfo.com.cn/"

    def query(self, q: Query) -> list[Hit]:
        req = urllib.request.Request(
            os.environ.get("CNINFO_ENDPOINT", self.endpoint),
            data=urllib.parse.urlencode(build_cninfo_params(q)).encode("utf-8"),
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Accept": "application/json,text/plain,*/*",
                "Origin": "https://www.cninfo.com.cn",
                "Referer": "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
                "User-Agent": "Mozilla/5.0 ir_search/0.1",
            },
            method="POST",
        )
        try:
            with open_url(req, timeout=int(os.environ.get("CNINFO_TIMEOUT", "20"))) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise AdapterError(http_error_message("cninfo request failed", exc), retryable=True) from exc
        except json.JSONDecodeError as exc:
            raise AdapterError(f"cninfo response is not JSON: {exc}", retryable=True) from exc
        except Exception as exc:
            raise AdapterError(f"cninfo request failed: {exc}", retryable=True) from exc

        hits = parse_cninfo_response(data, q.count)
        if not hits:
            raise AdapterError("cninfo returned no valid announcements", retryable=False)
        return hits


def build_cninfo_params(q: Query) -> dict[str, str]:
    _code, market = _security_from_query(q)
    column = {"SZ": "szse", "SH": "sse"}.get(market or "", "szse")
    return {
        "pageNum": "1",
        "pageSize": str(max(q.count, 10)),
        "column": column,
        "tabName": "fulltext",
        "plate": "",
        "stock": "",
        "searchkey": _searchkey(q),
        "secid": "",
        "category": "",
        "trade": "",
        "sortName": "time",
        "sortType": "desc",
        "isHLtitle": "true",
    }


def parse_cninfo_response(data: dict, count: int) -> list[Hit]:
    rows = data.get("announcements")
    if not isinstance(rows, list):
        raise AdapterError("cninfo response missing announcements list", retryable=True)

    hits: list[Hit] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        hit = cninfo_row_to_hit(row)
        if hit:
            hits.append(hit)
        if len(hits) >= count:
            break
    return hits


def cninfo_row_to_hit(row: dict) -> Optional[Hit]:
    title = _clean_title(row.get("announcementTitle") or row.get("title") or "")
    adjunct_url = row.get("adjunctUrl") or row.get("url") or ""
    if not title or not adjunct_url:
        return None

    security_code = row.get("secCode") or row.get("securityCode") or row.get("stockCode")
    security_name = _clean_title(row.get("secName") or row.get("securityName") or row.get("stockName") or "")
    announcement_type = _announcement_type(row)
    published_at = _parse_announcement_time(row.get("announcementTime") or row.get("announcementDate"))

    warnings = []
    for field, value in {
        "security_code": security_code,
        "security_name": security_name,
        "announcement_type": announcement_type,
        "published_at": published_at,
    }.items():
        if not value:
            warnings.append(f"missing_{field}")

    extra = {
        "adapter_mode": "live",
        "security_code": security_code,
        "security_name": security_name,
        "announcement_type": announcement_type,
        "market": _market_from_code(security_code),
        "source_platform": "cninfo",
    }
    if warnings:
        extra["parse_warning"] = ",".join(warnings)

    return Hit(
        title=title,
        url=_announcement_url(adjunct_url),
        snippet=announcement_type or title,
        source="cninfo",
        tier=SourceTier.EXCHANGE_FILING,
        evidence_type=_evidence_type(title, announcement_type),
        published_at=published_at,
        extra=extra,
    )


def _security_from_query(q: Query) -> tuple[Optional[str], Optional[str]]:
    for entity in q.entities:
        if entity.entity_type == EntityType.COMPANY:
            for code in entity.codes:
                match = re.search(r"(\d{6})(?:\.(SZ|SH))?", code, re.I)
                if match:
                    return match.group(1), (match.group(2) or _market_from_code(match.group(1)))
    match = re.search(r"(\d{6})(?:\.(SZ|SH))?", q.text, re.I)
    if match:
        return match.group(1), (match.group(2) or _market_from_code(match.group(1)))
    return None, None


def _searchkey(q: Query) -> str:
    code, _market = _security_from_query(q)
    company_name = None
    for entity in q.entities:
        if entity.entity_type == EntityType.COMPANY and entity.names:
            company_name = entity.names[0]
            break
    subject = code or company_name or q.text
    keywords = []
    for keyword in ["一季报", "三季报", "半年报", "年报", "业绩预告", "业绩快报", "问询函", "关注函"]:
        if keyword in q.text:
            keywords.append(keyword)
    return " ".join([subject] + keywords)


def _market_from_code(code: Optional[str]) -> Optional[str]:
    if not code:
        return None
    return "SH" if code.startswith(("5", "6", "9")) else "SZ"


def _announcement_url(adjunct_url: str) -> str:
    if adjunct_url.startswith("http"):
        return adjunct_url
    return urllib.parse.urljoin(CninfoAdapter.base_url, adjunct_url)


def _announcement_type(row: dict) -> str:
    value = row.get("announcementType") or row.get("categoryName") or row.get("announcementTypeName") or ""
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    return str(value)


def _parse_announcement_time(value: object) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000 if value > 10_000_000_000 else value, tz=timezone.utc)
    text = str(value).strip()
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"]:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _clean_title(value: str) -> str:
    value = re.sub(r"</?em>", "", value)
    return html.unescape(value).strip()


def _evidence_type(title: str, announcement_type: str) -> EvidenceType:
    text = f"{title} {announcement_type}".lower()
    if any(word in text for word in ["年报", "半年报", "季报", "一季报", "三季报", "业绩预告", "业绩快报"]):
        return EvidenceType.FINANCIAL_REPORT
    return EvidenceType.ANNOUNCEMENT
