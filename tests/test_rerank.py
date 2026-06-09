from datetime import datetime, timezone

from ir_search.models import EvidenceType, Hit, Intent, Query, SourceTier
from ir_search.rerank import rerank


def test_filing_prefers_exchange_filing_over_social():
    q = Query(text="中际旭创公告", intent=Intent.FILING, expanded_terms=["中际旭创", "公告"])
    hits = [
        Hit(
            title="雪球：中际旭创公告讨论",
            url="https://xueqiu.com/a",
            snippet="公告讨论",
            source="bocha",
            tier=SourceTier.UGC,
            evidence_type=EvidenceType.SOCIAL_POST,
            published_at=datetime.now(timezone.utc),
            canonical_url="xueqiu.com/a",
        ),
        Hit(
            title="中际旭创 关于经营情况的公告",
            url="https://www.cninfo.com.cn/a",
            snippet="中际旭创 公告",
            source="cninfo",
            tier=SourceTier.EXCHANGE_FILING,
            evidence_type=EvidenceType.ANNOUNCEMENT,
            published_at=datetime.now(timezone.utc),
            canonical_url="cninfo.com.cn/a",
        ),
    ]
    ranked = rerank(q, hits)
    assert ranked[0].source == "cninfo"


def test_policy_prefers_regulator_document():
    q = Query(text="出口管制 半导体", intent=Intent.POLICY, expanded_terms=["出口管制", "半导体"])
    hits = [
        Hit(
            title="媒体解读出口管制",
            url="https://news.example.com/a",
            snippet="半导体",
            source="bocha",
            tier=SourceTier.MEDIA,
            evidence_type=EvidenceType.NEWS,
            canonical_url="news.example.com/a",
        ),
        Hit(
            title="商务部关于半导体出口管制的通知",
            url="https://www.mofcom.gov.cn/a",
            snippet="出口管制 半导体 政策",
            source="regulator_sites",
            tier=SourceTier.REGULATOR,
            evidence_type=EvidenceType.POLICY_DOC,
            canonical_url="mofcom.gov.cn/a",
        ),
    ]
    assert rerank(q, hits)[0].source == "regulator_sites"
