from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from typing import Any, Callable

from ir_search.adapters.base import AdapterError
from ir_search.models import EntityType, EvidenceType, Hit, Query, SourceTier
from ir_search.network import http_error_message, open_url


TENCENT_ENDPOINT = "https://qt.gtimg.cn/q="
EASTMONEY_ENDPOINT = "https://push2.eastmoney.com/api/qt/stock/get"
BAIDU_QUOTE_ENDPOINT = "https://finance.pae.baidu.com/vapi/v1/getquotation"
THS_HOT_ENDPOINT_TEMPLATE = "http://zx.10jqka.com.cn/event/api/getharden/date/{date}/orderby/date/orderway/desc/charset/GBK/"
EXPLICIT_SYMBOL_RE = re.compile(r"\b[A-Z0-9]{1,10}\.(?:US|HK|SH|SZ|BJ)\b", re.IGNORECASE)
A_SHARE_RE = re.compile(r"(?<!\d)(?:[03648]\d{5})(?!\d)")
MAX_SYMBOLS = 10
PROVIDER_FETCHERS: dict[str, str] = {
    "tencent": "fetch_tencent_quotes",
    "eastmoney": "fetch_eastmoney_quotes",
    "baidu": "fetch_baidu_quotes",
    "akshare": "fetch_akshare_quotes",
    "mootdx": "fetch_mootdx_quotes",
    "ths": "fetch_ths_hot_reasons",
}


class MarketPublicAdapter:
    name = "market_public"
    mode = "live"

    def query(self, q: Query) -> list[Hit]:
        symbols = public_symbols_from_query(q)
        if not symbols:
            return []
        rows: list[dict[str, Any]] = []
        errors: dict[str, str] = {}
        for provider in configured_providers():
            fetcher = provider_fetcher(provider)
            if fetcher is None:
                errors[provider] = "unknown provider"
                continue
            try:
                rows.extend(fetcher(symbols[:MAX_SYMBOLS], q))
            except AdapterError as exc:
                errors[provider] = str(exc)
            except Exception as exc:
                errors[provider] = f"{type(exc).__name__}: {exc}"

        hits = [row_to_hit(row) for row in rows if row]
        if hits:
            if errors:
                for hit in hits:
                    hit.extra["provider_errors"] = errors
            return hits[: max(q.count, 10)]
        if errors:
            raise AdapterError("; ".join(f"{provider}: {error}" for provider, error in errors.items()), retryable=True)
        return []


def public_symbols_from_query(q: Query) -> list[str]:
    symbols: list[str] = []
    for entity in q.entities:
        if entity.entity_type == EntityType.COMPANY and entity.market == "A_SHARE":
            symbols.extend(code_to_public_symbol(code) for code in entity.codes)

    symbols.extend(normalize_explicit_symbol(match.group(0)) for match in EXPLICIT_SYMBOL_RE.finditer(q.text))
    symbols.extend(code_to_public_symbol(match.group(0)) for match in A_SHARE_RE.finditer(q.text))
    return [symbol for symbol in dict.fromkeys(symbols) if symbol]


def configured_providers() -> list[str]:
    raw = os.environ.get("MARKET_PUBLIC_PROVIDERS", "tencent,eastmoney,baidu,akshare,mootdx,ths")
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def provider_fetcher(provider: str) -> Callable[[list[str], Query], list[dict[str, Any]]] | None:
    name = PROVIDER_FETCHERS.get(provider)
    return globals().get(name) if name else None


def fetch_tencent_quotes(symbols: list[str], q: Query | None = None) -> list[dict[str, Any]]:
    tencent_codes = [public_symbol_to_tencent(symbol) for symbol in symbols]
    tencent_codes = [code for code in tencent_codes if code]
    if not tencent_codes:
        return []

    req = urllib.request.Request(
        TENCENT_ENDPOINT + ",".join(tencent_codes),
        headers={"User-Agent": "Mozilla/5.0"},
        method="GET",
    )
    try:
        with open_url(req, timeout=15) as resp:
            text = resp.read().decode("gbk", errors="replace")
    except urllib.error.HTTPError as exc:
        raise AdapterError(http_error_message("market_public Tencent quote failed", exc), retryable=True) from exc
    except Exception as exc:
        raise AdapterError(f"market_public Tencent quote failed: {exc}", retryable=True) from exc

    rows: list[dict[str, str]] = []
    for statement in text.split(";"):
        parsed = parse_tencent_statement(statement)
        if parsed:
            parsed["provider"] = "tencent"
            parsed["platform"] = "tencent_quote"
            parsed["url"] = f"https://gu.qq.com/{parsed.get('tencent_code')}"
            rows.append(parsed)
    return rows


def fetch_eastmoney_quotes(symbols: list[str], q: Query | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        secid = public_symbol_to_eastmoney_secid(symbol)
        if not secid:
            continue
        params = urllib.parse.urlencode(
            {
                "secid": secid,
                "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f60,f116,f117,f162,f167,f168,f169,f170",
            }
        )
        req = urllib.request.Request(
            f"{EASTMONEY_ENDPOINT}?{params}",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
            method="GET",
        )
        try:
            with open_url(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            raise AdapterError(http_error_message("market_public Eastmoney quote failed", exc), retryable=True) from exc
        except Exception as exc:
            raise AdapterError(f"market_public Eastmoney quote failed: {exc}", retryable=True) from exc
        row = parse_eastmoney_quote(data.get("data") or {}, symbol, secid)
        if row:
            rows.append(row)
    return rows


def parse_eastmoney_quote(data: dict[str, Any], symbol: str, secid: str = "") -> dict[str, Any]:
    if not data:
        return {}
    return {
        "provider": "eastmoney",
        "platform": "eastmoney_quote",
        "symbol": symbol,
        "eastmoney_secid": secid,
        "name": text_value(data.get("f58")),
        "last": scaled_value(data.get("f43"), 100),
        "pre_close": scaled_value(data.get("f60"), 100),
        "open": scaled_value(data.get("f46"), 100),
        "high": scaled_value(data.get("f44"), 100),
        "low": scaled_value(data.get("f45"), 100),
        "change": scaled_value(data.get("f169"), 100),
        "pct_chg": scaled_value(data.get("f170"), 100),
        "turnover_rate": scaled_value(data.get("f168"), 100),
        "pe_ttm": scaled_value(data.get("f162"), 100),
        "pb": scaled_value(data.get("f167"), 100),
        "total_mv_yi": yuan_to_yi(data.get("f116")),
        "float_mv_yi": yuan_to_yi(data.get("f117")),
        "url": eastmoney_quote_url(symbol),
        "raw": data,
    }


def fetch_baidu_quotes(symbols: list[str], q: Query | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        baidu_code = public_symbol_to_baidu_code(symbol)
        if not baidu_code:
            continue
        params = urllib.parse.urlencode(
            {
                "srcid": "5353",
                "pointType": "string",
                "group": "quotation_minute_ab",
                "market_type": "ab",
                "new_Format": "1",
                "finClientType": "pc",
                "query": baidu_code,
                "code": baidu_code,
            }
        )
        req = urllib.request.Request(
            f"{BAIDU_QUOTE_ENDPOINT}?{params}",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gushitong.baidu.com/"},
            method="GET",
        )
        try:
            with open_url(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            raise AdapterError(http_error_message("market_public Baidu quote failed", exc), retryable=True) from exc
        except Exception as exc:
            raise AdapterError(f"market_public Baidu quote failed: {exc}", retryable=True) from exc
        row = parse_baidu_quote(data, symbol, baidu_code)
        if row:
            rows.append(row)
    return rows


def parse_baidu_quote(data: dict[str, Any], symbol: str, baidu_code: str = "") -> dict[str, Any]:
    flat = flatten_json(data)
    if not flat:
        return {}
    name = first_flat(flat, ["name", "stockName", "shortName", "股票简称"])
    last = first_flat(flat, ["price", "latestPrice", "currentPrice", "curPrice", "close"])
    pct = first_flat(flat, ["ratio", "increaseRatio", "pctChg", "涨跌幅"])
    change = first_flat(flat, ["increase", "change", "涨跌额"])
    if not any([name, last, pct, change]):
        return {}
    return {
        "provider": "baidu",
        "platform": "baidu_stock",
        "symbol": symbol,
        "baidu_code": baidu_code,
        "name": text_value(name),
        "last": text_value(last),
        "pct_chg": text_value(pct),
        "change": text_value(change),
        "url": f"https://gushitong.baidu.com/stock/ab-{baidu_code}",
        "raw": data,
    }


def fetch_akshare_quotes(symbols: list[str], q: Query | None = None) -> list[dict[str, Any]]:
    try:
        import akshare as ak  # type: ignore
    except Exception as exc:
        raise AdapterError(f"akshare import failed: {exc}", retryable=False) from exc
    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        code = public_symbol_to_six_digit(symbol)
        if not code:
            continue
        try:
            df = ak.stock_individual_info_em(symbol=code)
        except Exception as exc:
            raise AdapterError(f"akshare stock_individual_info_em failed: {exc}", retryable=True) from exc
        info = dataframe_item_value_map(df)
        if not info:
            continue
        rows.append(
            {
                "provider": "akshare",
                "platform": "akshare_stock_individual_info_em",
                "symbol": symbol,
                "name": text_value(info.get("股票简称")),
                "total_mv_yi": yuan_to_yi(info.get("总市值")),
                "float_mv_yi": yuan_to_yi(info.get("流通市值")),
                "industry": text_value(info.get("行业")),
                "list_date": text_value(info.get("上市时间")),
                "url": f"https://quote.eastmoney.com/{eastmoney_quote_symbol(symbol)}.html",
                "raw": info,
            }
        )
    return rows


def fetch_mootdx_quotes(symbols: list[str], q: Query | None = None) -> list[dict[str, Any]]:
    try:
        from mootdx.quotes import Quotes  # type: ignore
    except Exception as exc:
        raise AdapterError(f"mootdx import failed: {exc}", retryable=False) from exc
    a_symbols = [public_symbol_to_six_digit(symbol) for symbol in symbols]
    a_symbols = [symbol for symbol in a_symbols if symbol]
    if not a_symbols:
        return []
    try:
        client = Quotes.factory(market="std")
        data = client.quotes(symbol=a_symbols)
    except Exception as exc:
        raise AdapterError(f"mootdx quotes failed: {exc}", retryable=True) from exc
    records = dataframe_records(data)
    rows: list[dict[str, Any]] = []
    for record in records:
        code = text_value(record.get("code") or record.get("symbol") or record.get("股票代码"))
        symbol = code_to_public_symbol(code) if code else ""
        rows.append(
            {
                "provider": "mootdx",
                "platform": "mootdx_quotes",
                "symbol": symbol or code,
                "name": text_value(record.get("name") or record.get("股票名称")),
                "last": text_value(record.get("price")),
                "pre_close": text_value(record.get("last_close")),
                "open": text_value(record.get("open")),
                "high": text_value(record.get("high")),
                "low": text_value(record.get("low")),
                "amount": text_value(record.get("amount")),
                "volume": text_value(record.get("vol") or record.get("volume")),
                "servertime": text_value(record.get("servertime")),
                "url": f"https://gu.qq.com/{public_symbol_to_tencent(symbol)}" if symbol else "https://gu.qq.com",
                "raw": record,
            }
        )
    return [row for row in rows if row.get("symbol")]


def fetch_ths_hot_reasons(symbols: list[str], q: Query | None = None) -> list[dict[str, Any]]:
    trade_date = date.today().strftime("%Y-%m-%d")
    req = urllib.request.Request(
        THS_HOT_ENDPOINT_TEMPLATE.format(date=trade_date),
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://zx.10jqka.com.cn/"},
        method="GET",
    )
    try:
        with open_url(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("gbk", errors="replace"))
    except urllib.error.HTTPError as exc:
        raise AdapterError(http_error_message("market_public THS hot reason failed", exc), retryable=True) from exc
    except Exception as exc:
        raise AdapterError(f"market_public THS hot reason failed: {exc}", retryable=True) from exc
    if str(data.get("errocode", 0)) not in {"0", ""}:
        raise AdapterError(f"market_public THS hot reason failed: {data.get('errormsg') or data}", retryable=True)
    wanted = {public_symbol_to_six_digit(symbol) for symbol in symbols}
    rows: list[dict[str, Any]] = []
    for item in data.get("data") or []:
        code = text_value(item.get("code"))
        if wanted and code not in wanted:
            continue
        rows.append(
            {
                "provider": "ths",
                "platform": "ths_hot_reason",
                "symbol": code_to_public_symbol(code),
                "name": text_value(item.get("name")),
                "last": text_value(item.get("close")),
                "pct_chg": text_value(item.get("zhangfu")),
                "change": text_value(item.get("zhangdie")),
                "turnover_rate": text_value(item.get("huanshou")),
                "amount": text_value(item.get("chengjiaoe")),
                "reason": text_value(item.get("reason")),
                "trade_date": trade_date,
                "url": "https://zx.10jqka.com.cn/",
                "raw": item,
            }
        )
    return rows


def parse_tencent_statement(statement: str) -> dict[str, str]:
    if '="' not in statement:
        return {}
    left, raw = statement.split('="', 1)
    code = left.rsplit("_", 1)[-1]
    values = raw.rstrip('"\n\r ').split("~")
    if len(values) < 4 or not values[1]:
        return {}
    return {
        "tencent_code": code,
        "symbol": tencent_code_to_public_symbol(code),
        "name": at(values, 1),
        "last": at(values, 3),
        "pre_close": at(values, 4),
        "open": at(values, 5),
        "change": at(values, 31),
        "pct_chg": at(values, 32),
        "high": at(values, 33),
        "low": at(values, 34),
        "amount_wan": at(values, 37),
        "turnover_rate": at(values, 38),
        "pe_ttm": at(values, 39),
        "amplitude": at(values, 43),
        "total_mv_yi": at(values, 44),
        "float_mv_yi": at(values, 45),
        "pb": at(values, 46),
        "limit_up": at(values, 47),
        "limit_down": at(values, 48),
        "volume_ratio": at(values, 49),
        "pe_static": at(values, 52),
        "raw_fields": values,
    }


def row_to_hit(row: dict[str, Any]) -> Hit:
    symbol = row.get("symbol") or row.get("tencent_code") or ""
    name = row.get("name") or ""
    snippet = "; ".join(
        f"{key}={row[key]}"
        for key in [
            "last",
            "pct_chg",
            "change",
            "turnover_rate",
            "pe_ttm",
            "pb",
            "total_mv_yi",
            "float_mv_yi",
            "limit_up",
            "limit_down",
            "industry",
            "reason",
            "servertime",
        ]
        if row.get(key)
    )
    provider = row.get("provider") or row.get("platform") or "public"
    return Hit(
        title=f"Market public {provider}: {symbol} {name}".strip(),
        url=row.get("url") or (f"https://gu.qq.com/{row.get('tencent_code')}" if row.get("tencent_code") else "https://gu.qq.com"),
        snippet=snippet,
        source=MarketPublicAdapter.name,
        tier=SourceTier.MEDIA,
        evidence_type=EvidenceType.DATA_TABLE,
        published_at=None,
        fetched_at=datetime.now(timezone.utc),
        extra={
            "platform": row.get("platform") or provider,
            "kind": "quote",
            "symbol": symbol,
            "provider": provider,
            "row": row,
            "requires_token": False,
            "read_only": True,
            "extraction_method": f"{provider}_public_market_api",
        },
    )


def normalize_explicit_symbol(symbol: str) -> str:
    text = symbol.strip().upper()
    if text.endswith(".HK"):
        code = text[:-3]
        return f"{code.zfill(5)}.HK" if code.isdigit() else text
    return text


def code_to_public_symbol(code: str) -> str:
    text = code.strip().upper()
    if EXPLICIT_SYMBOL_RE.fullmatch(text):
        return normalize_explicit_symbol(text)
    if re.fullmatch(r"[03648]\d{5}", text):
        if text.startswith("6"):
            return f"{text}.SH"
        if text.startswith(("8", "4")):
            return f"{text}.BJ"
        return f"{text}.SZ"
    return ""


def public_symbol_to_tencent(symbol: str) -> str:
    text = normalize_explicit_symbol(symbol)
    if text.endswith(".SH"):
        return "sh" + text[:-3]
    if text.endswith(".SZ"):
        return "sz" + text[:-3]
    if text.endswith(".BJ"):
        return "bj" + text[:-3]
    if text.endswith(".HK"):
        return "hk" + text[:-3].zfill(5)
    if text.endswith(".US"):
        return "us" + text[:-3]
    return ""


def public_symbol_to_eastmoney_secid(symbol: str) -> str:
    text = normalize_explicit_symbol(symbol)
    if text.endswith(".SH"):
        return "1." + text[:-3]
    if text.endswith(".SZ"):
        return "0." + text[:-3]
    if text.endswith(".BJ"):
        return "0." + text[:-3]
    if text.endswith(".HK"):
        return "116." + text[:-3].zfill(5)
    return ""


def public_symbol_to_baidu_code(symbol: str) -> str:
    text = normalize_explicit_symbol(symbol)
    if text.endswith(".SH"):
        return "sh" + text[:-3]
    if text.endswith(".SZ"):
        return "sz" + text[:-3]
    if text.endswith(".BJ"):
        return "bj" + text[:-3]
    return ""


def public_symbol_to_six_digit(symbol: str) -> str:
    text = normalize_explicit_symbol(symbol)
    if text.endswith((".SH", ".SZ", ".BJ")):
        return text[:-3]
    if re.fullmatch(r"[03648]\d{5}", text):
        return text
    return ""


def eastmoney_quote_symbol(symbol: str) -> str:
    secid = public_symbol_to_eastmoney_secid(symbol)
    if not secid:
        return symbol
    market, code = secid.split(".", 1)
    return ("sh" if market == "1" else "sz" if market == "0" else "hk") + code


def eastmoney_quote_url(symbol: str) -> str:
    return f"https://quote.eastmoney.com/{eastmoney_quote_symbol(symbol)}.html"


def tencent_code_to_public_symbol(code: str) -> str:
    lower = code.lower()
    if lower.startswith("sh"):
        return f"{code[2:]}.SH"
    if lower.startswith("sz"):
        return f"{code[2:]}.SZ"
    if lower.startswith("bj"):
        return f"{code[2:]}.BJ"
    if lower.startswith("hk"):
        return f"{code[2:].zfill(5)}.HK"
    if lower.startswith("us"):
        return f"{code[2:].upper()}.US"
    return code.upper()


def at(values: list[str], idx: int) -> str:
    return values[idx].strip() if idx < len(values) else ""


def scaled_value(value: Any, scale: float) -> str:
    if value in (None, "", "-", "--"):
        return ""
    try:
        return str(round(float(value) / scale, 4))
    except (TypeError, ValueError):
        return text_value(value)


def yuan_to_yi(value: Any) -> str:
    if value in (None, "", "-", "--"):
        return ""
    try:
        return str(round(float(value) / 100000000, 4))
    except (TypeError, ValueError):
        return text_value(value)


def text_value(value: Any) -> str:
    if value in (None, "", "-", "--"):
        return ""
    return str(value).strip()


def flatten_json(value: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, nested in value.items():
            dotted = f"{prefix}.{key}" if prefix else str(key)
            out[dotted] = nested
            out.update(flatten_json(nested, dotted))
    elif isinstance(value, list):
        for idx, nested in enumerate(value):
            out.update(flatten_json(nested, f"{prefix}.{idx}" if prefix else str(idx)))
    return out


def first_flat(flat: dict[str, Any], suffixes: list[str]) -> Any:
    for suffix in suffixes:
        for key, value in flat.items():
            if key.endswith(suffix) and value not in (None, "", "-", "--", [], {}):
                return value
    return ""


def dataframe_records(df: Any) -> list[dict[str, Any]]:
    if df is None:
        return []
    if hasattr(df, "to_dict"):
        try:
            return df.to_dict(orient="records")
        except TypeError:
            pass
    if isinstance(df, list):
        return [row for row in df if isinstance(row, dict)]
    return []


def dataframe_item_value_map(df: Any) -> dict[str, Any]:
    records = dataframe_records(df)
    if not records:
        return {}
    out: dict[str, Any] = {}
    for record in records:
        item = record.get("item") or record.get("项目")
        value = record.get("value") or record.get("值")
        if item not in (None, ""):
            out[str(item)] = value
    if out:
        return out
    if len(records) == 1:
        return records[0]
    return {str(idx): record for idx, record in enumerate(records)}
