from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ResearchPlan:
    question: str
    queries: list[str]
    intent: Optional[str]
    required_sources: list[str]


def plan_research_queries(
    question: str,
    *,
    intent: Optional[str] = None,
    max_searches: int = 8,
    allow_media: bool = True,
    allow_wechat: bool = True,
    allow_broker: bool = True,
) -> ResearchPlan:
    """Create bounded deterministic search queries for a research question."""

    max_searches = max(1, min(max_searches, 8))
    inferred_intent = None if intent in {None, "auto", ""} else intent
    queries = [question]
    if _looks_company_or_filing(question):
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
    return ResearchPlan(
        question=question,
        queries=deduped[:max_searches],
        intent=inferred_intent,
        required_sources=required_sources_for(question, inferred_intent),
    )


def required_sources_for(question: str, intent: Optional[str]) -> list[str]:
    lower = question.lower()
    if intent in {"filing", "earnings"} or _looks_company_or_filing(question):
        return ["cninfo", "sse", "szse", "hkex", "sec"]
    if "policy" in lower or _looks_policy(question):
        return ["regulator_sites"]
    return []


def _looks_company_or_filing(question: str) -> bool:
    return any(needle in question for needle in ["公告", "财报", "季报", "年报", "业绩", "公司"])


def _looks_policy(question: str) -> bool:
    return any(needle in question for needle in ["政策", "监管", "通知", "办法", "规则"])
