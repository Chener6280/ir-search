from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ir_search.models import EvidenceType, Hit, Query, SourceTier


class MockSearchAdapter:
    mode = "mock"

    def __init__(self, name: str) -> None:
        self.name = name

    def query(self, q: Query) -> list[Hit]:
        builder = BUILDERS.get(self.name, _generic_hits)
        return builder(q, self.name)[: q.count]


def _company(q: Query) -> str:
    if q.entities:
        return q.entities[0].names[0]
    return q.text


def _dt(days_ago: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


def _filing_hits(q: Query, source: str) -> list[Hit]:
    company = _company(q)
    domain = {
        "cninfo": "cninfo.com.cn",
        "sse": "sse.com.cn",
        "szse": "szse.cn",
        "hkex": "hkexnews.hk",
        "sec": "sec.gov",
    }.get(source, "cninfo.com.cn")
    return [
        Hit(
            title=f"{company} 2026年第一季度报告",
            url=f"https://www.{domain}/disclosure/{company}/2026-q1",
            snippet=f"{company} 披露一季报，包含收入、毛利率、净利润和经营现金流。",
            source=source,
            tier=SourceTier.EXCHANGE_FILING,
            evidence_type=EvidenceType.FINANCIAL_REPORT,
            published_at=_dt(35),
        ),
        Hit(
            title=f"{company} 关于经营情况的公告",
            url=f"https://www.{domain}/disclosure/{company}/announcement",
            snippet=f"{company} 最新公告，涉及业务进展和风险提示。",
            source=source,
            tier=SourceTier.EXCHANGE_FILING,
            evidence_type=EvidenceType.ANNOUNCEMENT,
            published_at=_dt(7),
        ),
    ]


def _bocha_hits(q: Query, source: str) -> list[Hit]:
    company = _company(q)
    return [
        Hit(
            title=f"{company} 最新投研跟踪：光模块需求维持高景气",
            url="https://www.stcn.com/article/mock-ir-search",
            snippet=f"{company} 与 800G、1.6T、CPO 等光模块产业链需求相关，机构关注海外云厂商资本开支。",
            source=source,
            tier=SourceTier.MEDIA,
            evidence_type=EvidenceType.NEWS,
            published_at=_dt(3),
        ),
        Hit(
            title=f"雪球讨论：{company} 一季报怎么看",
            url="https://xueqiu.com/mock/status/1?utm_source=test",
            snippet="投资者讨论业绩弹性、估值和订单节奏，信息噪声较高。",
            source=source,
            tier=SourceTier.UGC,
            evidence_type=EvidenceType.SOCIAL_POST,
            published_at=_dt(1),
        ),
    ]


def _exa_hits(q: Query, source: str) -> list[Hit]:
    return [
        Hit(
            title="NVIDIA capex and optical transceiver supply chain implications",
            url="https://www.example.com/research/nvidia-capex-optical-modules",
            snippet="Cloud AI capex can affect optical transceiver demand, 800G upgrades, and CPO roadmap expectations.",
            source=source,
            tier=SourceTier.MEDIA,
            evidence_type=EvidenceType.NEWS,
            published_at=_dt(6),
            raw_score=0.82,
        ),
        Hit(
            title="SEC filing: AI infrastructure capital expenditure discussion",
            url="https://www.sec.gov/Archives/mock-ai-capex",
            snippet="Company filing discusses capital expenditure plans and AI infrastructure investment.",
            source=source,
            tier=SourceTier.EXCHANGE_FILING,
            evidence_type=EvidenceType.ANNOUNCEMENT,
            published_at=_dt(50),
            raw_score=0.74,
        ),
    ]


def _broker_hits(q: Query, source: str) -> list[Hit]:
    company = _company(q)
    return [
        Hit(
            title=f"XX证券：{company} 首次覆盖，800G 需求驱动盈利预测上修",
            url="https://research.example.com/report/zhongji-innolight",
            snippet=f"分析师维持买入评级，讨论 {company} 毛利率、目标价和盈利预测。",
            source=source,
            tier=SourceTier.BROKER,
            evidence_type=EvidenceType.BROKER_REPORT,
            published_at=_dt(2),
        )
    ]


def _policy_hits(q: Query, source: str) -> list[Hit]:
    return [
        Hit(
            title="商务部关于半导体相关出口管制的通知",
            url="https://www.mofcom.gov.cn/article/mock-export-control",
            snippet="监管部门发布出口管制政策文件，涉及半导体产业链合规要求。",
            source=source,
            tier=SourceTier.REGULATOR,
            evidence_type=EvidenceType.POLICY_DOC,
            published_at=_dt(20),
        )
    ]


def _company_ir_hits(q: Query, source: str) -> list[Hit]:
    company = _company(q)
    return [
        Hit(
            title=f"{company} 投资者关系活动记录表",
            url="https://ir.example.com/activity-record",
            snippet=f"{company} 回答投资者关于订单、产能和产品节奏的问题。",
            source=source,
            tier=SourceTier.COMPANY,
            evidence_type=EvidenceType.EARNINGS_CALL,
            published_at=_dt(12),
        )
    ]


def _generic_hits(q: Query, source: str) -> list[Hit]:
    return [
        Hit(
            title=f"{source} result for {q.text}",
            url=f"https://{source}.example.com/search/result",
            snippet=f"Mock result from {source}: {q.text}",
            source=source,
            published_at=_dt(15),
        )
    ]


BUILDERS = {
    "cninfo": _filing_hits,
    "sse": _filing_hits,
    "szse": _filing_hits,
    "hkex": _filing_hits,
    "sec": _filing_hits,
    "bocha": _bocha_hits,
    "exa": _exa_hits,
    "broker_research": _broker_hits,
    "regulator_sites": _policy_hits,
    "company_ir": _company_ir_hits,
    "industry_media": _bocha_hits,
}
