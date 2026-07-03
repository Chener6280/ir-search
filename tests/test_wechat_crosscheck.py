from __future__ import annotations

from ir_search.evidence.models import ClaimVerification, EvidenceSpan
from ir_search.models import EvidenceType, SourceTier
from ir_search.research.orchestrator import build_wechat_crosscheck


def test_wechat_crosscheck_candidate_only_when_only_wechat_evidence():
    span = EvidenceSpan(
        span_id="sp_wechat",
        doc_id="doc1",
        url="https://mp.weixin.qq.com/s/a",
        title="公众号涨价传闻",
        source="wechat_opencli",
        source_tier=SourceTier.MEDIA,
        evidence_type=EvidenceType.OPINION,
        text="产业链涨价传闻。",
        relevance_score=0.5,
    )

    crosscheck = build_wechat_crosscheck("微信公众号提到产业链涨价", [span], [ClaimVerification("c1", "涨价传闻", "mixed", 0.4, supporting_spans=[span])])

    assert crosscheck["applicable"] is True
    assert crosscheck["verdict"] == "candidate_only"
    assert len(crosscheck["wechat_evidence"]) == 1


def test_wechat_crosscheck_supported_by_official_when_primary_support_exists():
    official = EvidenceSpan(
        span_id="sp_official",
        doc_id="doc2",
        url="https://www.cninfo.com.cn/a.pdf",
        title="公告",
        source="cninfo",
        source_tier=SourceTier.EXCHANGE_FILING,
        evidence_type=EvidenceType.ANNOUNCEMENT,
        text="公司公告披露价格调整。",
        relevance_score=0.8,
    )

    crosscheck = build_wechat_crosscheck(
        "微信公众号提到产业链涨价",
        [official],
        [ClaimVerification("c1", "价格调整", "supported", 0.8, supporting_spans=[official])],
    )

    assert crosscheck["verdict"] == "supported_by_official"
