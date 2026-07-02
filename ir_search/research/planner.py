from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ResearchPlan:
    question: str
    queries: list[str]
    intent: Optional[str]
    required_sources: list[str]
    warnings: list[str]


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
    )


def required_sources_for(question: str, intent: Optional[str]) -> list[str]:
    lower = question.lower()
    if intent in {"filing", "earnings"} or _looks_company_or_filing(question):
        return ["cninfo", "company_ir", "sse", "szse", "hkex", "sec"]
    if "policy" in lower or _looks_policy(question):
        return ["regulator_sites"]
    if intent == "wechat_crosscheck":
        return ["manual_wechat", "wechat_opencli", "cninfo", "company_ir"]
    return []


def normalize_research_intent(question: str, intent: Optional[str]) -> Optional[str]:
    if intent not in {None, "auto", ""}:
        return str(intent).lower()
    if "公众号" in question or "微信" in question:
        return "wechat_crosscheck"
    if _looks_policy(question):
        return "policy"
    if _looks_company_or_filing(question):
        return "earnings"
    if any(needle in question for needle in ["产业链", "供需", "价格", "需求", "产能"]):
        return "industry_chain"
    return None


def _looks_company_or_filing(question: str) -> bool:
    return any(needle in question for needle in ["公告", "财报", "季报", "年报", "业绩", "公司"])


def _looks_policy(question: str) -> bool:
    return any(needle in question for needle in ["政策", "监管", "通知", "办法", "规则"])
