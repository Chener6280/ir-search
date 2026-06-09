from ir_search.evidence import classify_hit
from ir_search.models import EvidenceType, Hit, SourceTier


def test_cninfo_financial_report_classification():
    hit = classify_hit(
        Hit(
            title="中际旭创 2026年第一季度报告",
            url="https://www.cninfo.com.cn/disclosure/q1",
            snippet="一季报",
            source="cninfo",
        )
    )
    assert hit.tier == SourceTier.EXCHANGE_FILING
    assert hit.evidence_type == EvidenceType.FINANCIAL_REPORT


def test_broker_report_by_title_features():
    hit = classify_hit(
        Hit(
            title="XX证券：首次覆盖，维持买入，目标价上调",
            url="https://research.example.com/a",
            snippet="盈利预测和投资评级",
            source="bocha",
        )
    )
    assert hit.evidence_type == EvidenceType.BROKER_REPORT
