from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Optional

from ir_search.adapters.base import AdapterError
from ir_search.models import EntityType, EvidenceType, Hit, Query, SourceTier
from ir_search.network import http_error_message, open_url


ENDPOINT_DEFAULT = "https://fastapic.stockai888.top"
MAX_SYMBOLS = 5

EXPLICIT_TS_CODE_RE = re.compile(r"\b(?:[036]\d{5})\.(?:SH|SZ)\b", re.IGNORECASE)
A_SHARE_CODE_RE = re.compile(r"(?<!\d)(?:[03648]\d{5})(?!\d)")

FIELDS = {
    "stock_basic": "ts_code,symbol,name,area,industry,market,exchange,list_date",
    "daily": "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount",
    "daily_basic": "ts_code,trade_date,close,turnover_rate,volume_ratio,pe,pb,ps,total_mv,circ_mv",
    "fina_indicator": "ts_code,ann_date,end_date,eps,dt_eps,roe,roa,grossprofit_margin,netprofit_margin,or_yoy,netprofit_yoy",
    "forecast": "ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max,summary,change_reason",
    "express": "ts_code,ann_date,end_date,revenue,operate_profit,total_profit,n_income,total_assets,diluted_eps,diluted_roe,yoy_sales,yoy_op,yoy_tp,yoy_dedu_np",
    "stk_holdernumber": "ts_code,ann_date,end_date,holder_num",
    "top_list": "trade_date,ts_code,name,close,pct_change,turnover_rate,amount,l_sell,l_buy,l_amount,net_amount,reason",
    "share_float": "ts_code,ann_date,float_date,float_share,float_ratio,holder_name,share_type",
    "moneyflow": "ts_code,trade_date,buy_sm_amount,sell_sm_amount,buy_md_amount,sell_md_amount,buy_lg_amount,sell_lg_amount,buy_elg_amount,sell_elg_amount,net_mf_amount",
}

DISPLAY_NAMES = {
    "stock_basic": "A股基础资料",
    "daily": "日行情",
    "daily_basic": "每日指标",
    "fina_indicator": "财务指标",
    "forecast": "业绩预告",
    "express": "业绩快报",
    "stk_holdernumber": "股东人数",
    "top_list": "龙虎榜",
    "share_float": "限售解禁",
    "moneyflow": "资金流向",
}


class TushareAdapter:
    name = "tushare"
    mode = "live"

    def query(self, q: Query) -> list[Hit]:
        token = os.environ.get("TUSHARE_TOKEN") or os.environ.get("TUSHARE_PRO_TOKEN")
        if not token:
            raise AdapterError("TUSHARE_TOKEN or TUSHARE_PRO_TOKEN is not set", retryable=False)

        client = TushareClient(token=token, endpoint=os.environ.get("TUSHARE_HTTP_URL", ENDPOINT_DEFAULT))
        symbols = symbols_from_query(q)
        hits: list[Hit] = []

        if not symbols:
            symbols = lookup_symbols(client, q)[:MAX_SYMBOLS]
        if not symbols:
            return []

        apis = select_apis(q.text)
        for symbol in symbols[:MAX_SYMBOLS]:
            for api_name in apis:
                params = params_for(api_name, symbol)
                rows = client.query(api_name, params=params, fields=FIELDS[api_name])
                hits.extend(rows_to_hits(api_name, rows[: min(q.count, 10)], symbol))

        return hits[: max(q.count, 10)]


class TushareClient:
    def __init__(self, token: str, endpoint: str = ENDPOINT_DEFAULT) -> None:
        self.token = token
        self.endpoint = endpoint

    def query(self, api_name: str, params: dict[str, Any], fields: str) -> list[dict[str, Any]]:
        pause = float(os.environ.get("TUSHARE_RATE_LIMIT_SECONDS", "0.65") or 0)
        if pause > 0:
            time.sleep(pause)
        payload = {
            "api_name": api_name,
            "token": self.token,
            "params": params,
            "fields": fields,
        }
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Accept-Encoding": "gzip", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with open_url(req, timeout=int(os.environ.get("TUSHARE_TIMEOUT", "20")), proxy_url=os.environ.get("TUSHARE_PROXY")) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise AdapterError(http_error_message("tushare request failed", exc), retryable=True) from exc
        except Exception as exc:
            raise AdapterError(f"tushare request failed: {exc}", retryable=True) from exc

        code = data.get("code", 0)
        if code not in (0, "0"):
            message = data.get("msg") or "unknown error"
            retryable = any(needle in str(message).lower() for needle in ["timeout", "频繁", "超时", "network", "429"])
            raise AdapterError(f"tushare {api_name} failed: code={code}, msg={message}", retryable=retryable)

        payload_data = data.get("data") or {}
        return table_to_rows(payload_data.get("fields") or [], payload_data.get("items") or [])


def symbols_from_query(q: Query) -> list[str]:
    symbols: list[str] = []
    for entity in q.entities:
        if entity.entity_type == EntityType.COMPANY and entity.market == "A_SHARE":
            symbols.extend(code_to_ts_code(code) for code in entity.codes)

    symbols.extend(match.group(0).upper() for match in EXPLICIT_TS_CODE_RE.finditer(q.text))
    symbols.extend(code_to_ts_code(match.group(0)) for match in A_SHARE_CODE_RE.finditer(q.text))
    return [symbol for symbol in dict.fromkeys(symbols) if symbol]


def lookup_symbols(client: TushareClient, q: Query) -> list[str]:
    symbols: list[str] = []
    for name in company_name_candidates(q.text):
        rows = client.query("stock_basic", params={"name": name, "list_status": "L"}, fields=FIELDS["stock_basic"])
        symbols.extend(str(row.get("ts_code") or "") for row in rows)
    return [symbol for symbol in dict.fromkeys(symbols) if symbol]


def company_name_candidates(text: str) -> list[str]:
    stopwords = {
        "tushare",
        "TuShare",
        "最新",
        "数据",
        "行情",
        "日行情",
        "财务",
        "财务指标",
        "业绩",
        "预告",
        "快报",
        "股东人数",
        "龙虎榜",
        "解禁",
        "限售",
        "资金流",
        "估值",
    }
    tokens = re.split(r"[\s,，。:：/]+", text)
    candidates: list[str] = []
    for token in tokens:
        cleaned = token.strip()
        if not cleaned or cleaned in stopwords or cleaned.lower() == "tushare":
            continue
        if EXPLICIT_TS_CODE_RE.search(cleaned) or A_SHARE_CODE_RE.fullmatch(cleaned):
            continue
        if 2 <= len(cleaned) <= 12 and any("\u4e00" <= ch <= "\u9fff" for ch in cleaned):
            candidates.append(cleaned)
    return list(dict.fromkeys(candidates))[:3]


def code_to_ts_code(code: str) -> str:
    text = code.strip().upper()
    if EXPLICIT_TS_CODE_RE.fullmatch(text):
        return text
    if re.fullmatch(r"[03648]\d{5}", text):
        if text.startswith(("6", "9")):
            return f"{text}.SH"
        if text.startswith(("8", "4")):
            return f"{text}.BJ"
        return f"{text}.SZ"
    return ""


def select_apis(text: str) -> list[str]:
    lower = text.lower()
    selected: list[str] = ["stock_basic"]
    keyword_map = [
        (["日行情", "行情", "涨跌", "k线", "k 线", "ohlc"], ["daily"]),
        (["估值", "换手", "换手率", "pe", "pb", "ps", "市值", "量比"], ["daily_basic"]),
        (["财务", "财务指标", "roe", "roa", "eps", "毛利率", "净利率", "营收", "利润"], ["fina_indicator"]),
        (["业绩预告", "预告"], ["forecast"]),
        (["业绩快报", "快报"], ["express"]),
        (["股东人数", "股东户数", "户数"], ["stk_holdernumber"]),
        (["龙虎榜"], ["top_list"]),
        (["解禁", "限售"], ["share_float"]),
        (["资金流", "主力", "净流入"], ["moneyflow"]),
    ]
    for needles, apis in keyword_map:
        if any(needle in lower or needle in text for needle in needles):
            selected.extend(apis)

    if selected == ["stock_basic"]:
        selected.extend(["daily_basic", "fina_indicator"])
    return list(dict.fromkeys(selected))[:4]


def params_for(api_name: str, symbol: str) -> dict[str, Any]:
    if api_name == "stock_basic":
        return {"ts_code": symbol, "list_status": "L"}
    if api_name in {"daily", "daily_basic", "moneyflow", "top_list"}:
        return {"ts_code": symbol, "start_date": date_n_days_ago(30), "end_date": today_yyyymmdd()}
    if api_name in {"fina_indicator", "forecast", "express", "stk_holdernumber", "share_float"}:
        return {"ts_code": symbol, "start_date": date_n_days_ago(730), "end_date": today_yyyymmdd()}
    return {"ts_code": symbol}


def rows_to_hits(api_name: str, rows: list[dict[str, Any]], symbol: str) -> list[Hit]:
    hits: list[Hit] = []
    for row in rows:
        row_symbol = str(row.get("ts_code") or symbol)
        row_date = first_text(row, ["trade_date", "ann_date", "end_date", "float_date", "list_date"])
        hits.append(
            Hit(
                title=tushare_title(api_name, row_symbol, row),
                url=tushare_url(api_name, row_symbol),
                snippet=row_snippet(row),
                source=TushareAdapter.name,
                tier=SourceTier.MEDIA,
                evidence_type=EvidenceType.DATA_TABLE,
                published_at=parse_yyyymmdd(row_date),
                extra={
                    "platform": "tushare",
                    "kind": api_name,
                    "api_name": api_name,
                    "symbol": row_symbol,
                    "row": row,
                    "requires_token": True,
                    "read_only": True,
                    "extraction_method": "tushare_http_api",
                },
            )
        )
    return hits


def table_to_rows(fields: list[str], items: list[list[Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        rows.append({field: value for field, value in zip(fields, item)})
    return rows


def tushare_title(api_name: str, symbol: str, row: dict[str, Any]) -> str:
    name = first_text(row, ["name"])
    label = DISPLAY_NAMES.get(api_name, api_name)
    date = first_text(row, ["trade_date", "ann_date", "end_date", "float_date"])
    subject = f"{symbol} {name}".strip()
    return f"TuShare {label}: {subject}" + (f" ({date})" if date else "")


def row_snippet(row: dict[str, Any]) -> str:
    return "; ".join(f"{key}={value}" for key, value in row.items() if value not in (None, ""))


def first_text(row: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def tushare_url(api_name: str, symbol: str) -> str:
    return f"https://tushare.pro/document/2?api={api_name}&symbol={symbol}"


def today_yyyymmdd() -> str:
    return datetime.now().strftime("%Y%m%d")


def date_n_days_ago(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")


def parse_yyyymmdd(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = str(value)
    if not re.fullmatch(r"\d{8}", text):
        return None
    try:
        return datetime.strptime(text, "%Y%m%d")
    except ValueError:
        return None
