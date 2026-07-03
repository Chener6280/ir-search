from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ResearchPlan:
    question: str
    queries: list[str]
    intent: Optional[str]
    required_sources: list[str]
    warnings: list[str]
    official_queries: list[str] = field(default_factory=list)


def plan_research_queries(
    question: str,
    *,
    intent: Optional[str] = None,
    max_searches: int = 8,
    allow_media: bool = True,
    allow_wechat: bool = True,
    allow_broker: bool = True,
    source_health: Optional[dict] = None,
) -> ResearchPlan:
    """Create bounded deterministic search queries for a research question."""

    warnings: list[str] = []
    if max_searches > 8:
        warnings.append("max_searches clamped to 8")
    max_searches = max(1, min(max_searches, 8))
    inferred_intent = normalize_research_intent(question, intent)
    queries = [question]
    if inferred_intent in {"filing", "earnings"}:
        queries.extend(
            [
                f"{question} 官方公告 财报",
                f"{question} cninfo 交易所 披露",
                f"{question} 投资者关系 业绩说明",
            ]
        )
    elif inferred_intent == "policy":
        queries.extend(
            [
                f"{question} 政策 原文 监管",
                f"{question} 官方通知 执行时间 适用范围",
            ]
        )
    elif inferred_intent == "industry_chain":
        queries.extend(
            [
                f"{question} 需求 价格 供给 产能",
                f"{question} 公司公告 新闻 交叉验证",
            ]
        )
    elif inferred_intent == "overseas_mapping":
        queries.extend(
            [
                f"{question} overseas company IR earnings call",
                f"{question} SEC filing earnings transcript capex demand",
            ]
        )
    elif inferred_intent == "wechat_crosscheck":
        queries.extend(
            [
                f"{question} 公众号 候选来源",
                f"{question} 官方公告 公司新闻 交叉验证",
            ]
        )
    elif _looks_company_or_filing(question):
        queries.append(f"{question} 官方公告 财报")
    if _looks_policy(question):
        queries.append(f"{question} 政策 原文 监管")
    if allow_broker:
        queries.append(f"{question} 研报 观点 风险")
    if allow_media:
        queries.append(f"{question} 新闻 进展")
    if allow_wechat and ("公众号" in question or "微信" in question):
        queries.append(f"{question} 公众号")
    queries.append(f"{question} 反驳 否认 风险")
    deduped = []
    for query in queries:
        if query not in deduped:
            deduped.append(query)
    required_sources = required_sources_for(question, inferred_intent)
    official_queries = official_queries_for(question, inferred_intent)
    for source in required_sources:
        status = (source_health or {}).get("sources", {}).get(source, {})
        if status.get("adapter_mode") in {"mock", "placeholder"}:
            warnings.append(f"{source} is {status.get('adapter_mode')}; official source unavailable")
    return ResearchPlan(
        question=question,
        queries=deduped[:max_searches],
        intent=inferred_intent,
        required_sources=required_sources,
        warnings=warnings,
        official_queries=official_queries,
    )


def required_sources_for(question: str, intent: Optional[str]) -> list[str]:
    lower = question.lower()
    if intent in {"filing", "earnings"} or _looks_company_or_filing(question):
        return ["cninfo", "company_ir", "sse", "szse", "hkex", "sec"]
    if intent == "overseas_mapping" or _looks_overseas_ai_optical_demand(question):
        sources = ["company_ir", "sec"]
        if _looks_china_supply_chain_question(question):
            sources.append("cninfo")
        return _ordered_unique(sources)
    if "policy" in lower or _looks_policy(question):
        return ["regulator_sites"]
    if intent == "wechat_crosscheck":
        return ["manual_wechat", "wechat_opencli", "cninfo", "company_ir"]
    if intent == "industry_chain" and _current_info_requires_official_evidence(question):
        sources = ["company_ir"]
        if _looks_china_supply_chain_question(question):
            sources.insert(0, "cninfo")
        if _looks_overseas_or_english_company_question(question):
            sources.append("sec")
        return _ordered_unique(sources)
    if _looks_official_evidence_request(question):
        return ["cninfo", "company_ir", "sse", "szse", "hkex", "sec", "regulator_sites"]
    return []


def normalize_research_intent(question: str, intent: Optional[str]) -> Optional[str]:
    if intent not in {None, "auto", ""}:
        return str(intent).lower()
    if "公众号" in question or "微信" in question:
        return "wechat_crosscheck"
    if _looks_policy(question):
        return "policy"
    if _looks_overseas_ai_optical_demand(question) or _looks_overseas_mapping(question):
        return "overseas_mapping"
    if _looks_company_or_filing(question):
        return "earnings"
    if any(needle in question for needle in ["产业链", "供需", "价格", "需求", "产能"]):
        return "industry_chain"
    return None


def official_queries_for(question: str, intent: Optional[str]) -> list[str]:
    """Build deterministic official-first query attempts for auditability."""

    if not required_sources_for(question, intent):
        return []
    queries: list[str] = []
    if _looks_china_supply_chain_question(question) or _looks_company_or_filing(question):
        queries.append(f"{question} 巨潮资讯 cninfo 公告 披露")
    if _looks_overseas_ai_optical_demand(question):
        queries.extend(OVERSEAS_AI_OPTICAL_DEMAND_OFFICIAL_QUERIES)
    if _looks_company_or_filing(question):
        queries.extend(
            [
                f"{question} 投资者关系 业绩说明 官方",
                f"{question} 交易所 公告 财报",
            ]
        )
    if _current_info_requires_official_evidence(question):
        queries.append(f"{question} official company IR earnings call SEC filing")
    return _ordered_unique(queries)


def _looks_company_or_filing(question: str) -> bool:
    return any(needle in question for needle in ["公告", "财报", "季报", "年报", "业绩", "公司"])


def _looks_policy(question: str) -> bool:
    return any(needle in question for needle in ["政策", "监管", "通知", "办法", "规则"])


def _looks_official_evidence_request(question: str) -> bool:
    lower = question.lower()
    return any(
        needle in question
        for needle in ["官方", "一手", "确认", "证实", "披露", "交易所", "公开证据"]
    ) or any(
        needle in lower
        for needle in ["company filing", "official evidence", "primary source", "confirmed by official"]
    )


def _current_info_requires_official_evidence(question: str) -> bool:
    if not _looks_current_information_request(question):
        return _looks_official_evidence_request(question)
    return (
        _looks_official_evidence_request(question)
        or _looks_company_or_filing(question)
        or any(needle in question for needle in ["需求", "订单", "供需", "产能", "产业链", "光模块"])
        or any(needle in question.lower() for needle in ["demand", "order", "supply chain", "capex", "backlog"])
    )


def _looks_current_information_request(question: str) -> bool:
    lower = question.lower()
    return any(
        needle in question or needle in lower
        for needle in [
            "最近",
            "最新",
            "当前",
            "近30天",
            "近 30 天",
            "近90天",
            "近 90 天",
            "本月",
            "本季度",
            "2026",
            "latest",
            "recent",
            "current",
            "90d",
        ]
    )


def _looks_overseas_mapping(question: str) -> bool:
    lower = question.lower()
    return any(needle in question for needle in ["海外", "英伟达"]) or any(
        needle in lower for needle in ["overseas", "nvidia", "capex", "sec filing"]
    )


def _looks_overseas_ai_optical_demand(question: str) -> bool:
    lower = question.lower()
    has_optical = any(needle in question for needle in ["光模块", "CPO"]) or any(
        needle in lower for needle in ["optical module", "optical", "cpo", "800g", "1.6t"]
    )
    has_ai_or_overseas = any(needle in question for needle in ["海外", "AI", "算力"]) or any(
        needle in lower for needle in ["overseas", "ai", "datacenter", "data center", "cloud"]
    )
    has_demand = any(needle in question for needle in ["需求", "订单"]) or any(
        needle in lower for needle in ["demand", "backlog", "capex", "revenue"]
    )
    return has_optical and has_ai_or_overseas and has_demand


def _looks_china_supply_chain_question(question: str) -> bool:
    lower = question.lower()
    return any(
        needle in question
        for needle in ["A股", "中国", "国内", "中际旭创", "新易盛", "天孚通信", "光模块", "产业链"]
    ) or any(needle in lower for needle in ["a-share", "china supply chain", "china-listed"])


def _looks_overseas_or_english_company_question(question: str) -> bool:
    lower = question.lower()
    return _looks_overseas_mapping(question) or any(
        needle in lower
        for needle in [
            "coherent",
            "lumentum",
            "fabrinet",
            "microsoft",
            "meta",
            "google",
            "amazon",
            "sec",
        ]
    )


def _ordered_unique(items: list[str]) -> list[str]:
    deduped: list[str] = []
    for item in items:
        if item and item not in deduped:
            deduped.append(item)
    return deduped


OVERSEAS_AI_OPTICAL_DEMAND_OFFICIAL_QUERIES = [
    "Coherent AI optical demand earnings call",
    "Lumentum datacom AI demand backlog",
    "Fabrinet optical communications AI datacenter revenue",
    "Microsoft Meta Google Amazon capex networking AI optical",
]
