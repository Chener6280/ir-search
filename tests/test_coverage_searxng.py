from ir_search.evidence import classify_hit
from ir_search.models import EvidenceType, Hit, Query
from ir_search.pipeline import run_pipeline


class SearXNGDiscoveryAdapter:
    name = "searxng"
    mode = "fallback"

    def query(self, q: Query):
        return [
            Hit(
                title="XX证券：首次覆盖，维持买入",
                url="https://example.com/reposted-report-title",
                snippet="搜索结果摘要，不是正文",
                source=self.name,
                extra={
                    "coverage_status": "partial",
                    "result_kind": "discovery_url",
                    "evidence_type": "search_result",
                    "confidence": "low_to_medium",
                    "query": q.text,
                    "engine": "bing",
                    "rank": 1,
                },
            )
        ]


def test_searxng_search_result_stays_partial_not_covered():
    result = run_pipeline(
        Query(text="XX证券 光模块 首次覆盖", sources=["searxng"]),
        {"searxng": SearXNGDiscoveryAdapter()},
    )

    hit = result.hits[0]
    assert hit.source == "searxng"
    assert hit.evidence_type == EvidenceType.UNKNOWN
    assert hit.extra["coverage_status"] == "partial"
    assert hit.extra["evidence_type"] == "search_result"
    assert hit.extra["coverage_status"] != "covered"


def test_search_result_marker_blocks_title_based_evidence_upgrade():
    hit = classify_hit(
        Hit(
            title="XX证券：首次覆盖，维持买入，目标价上调",
            url="https://research.example.com/title-only",
            snippet="盈利预测和投资评级",
            source="searxng",
            extra={"coverage_status": "partial_discovery", "evidence_type": "search_result"},
        )
    )

    assert hit.evidence_type == EvidenceType.UNKNOWN
