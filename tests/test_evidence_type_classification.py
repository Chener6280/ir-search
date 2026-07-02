from __future__ import annotations

from ir_search.documents.fetcher import document_from_hit, normalize_evidence_type_for_source
from ir_search.models import EvidenceType, Hit, SourceTier


def test_media_source_not_financial_report():
    evidence_type, warnings = normalize_evidence_type_for_source(
        source_tier=SourceTier.MEDIA,
        source="web",
        url="https://example.com/news",
        title="公司年报解读",
        content_type="html",
        current_evidence_type=EvidenceType.FINANCIAL_REPORT,
    )

    assert evidence_type == EvidenceType.NEWS
    assert warnings


def test_stcn_downgraded_to_news():
    evidence_type, _warnings = normalize_evidence_type_for_source(
        source_tier=SourceTier.MEDIA,
        source="bocha",
        url="https://www.stcn.com/article/detail/123.html",
        title="证券时报：公司年报发布",
        content_type="html",
        current_evidence_type=EvidenceType.FINANCIAL_REPORT,
    )

    assert evidence_type == EvidenceType.NEWS


def test_cnstock_downgraded_to_news():
    evidence_type, _warnings = normalize_evidence_type_for_source(
        source_tier=SourceTier.MEDIA,
        source="bocha",
        url="https://news.cnstock.com/news,bwkx-202607-123.htm",
        title="上证报报道季报",
        content_type="html",
        current_evidence_type=EvidenceType.FINANCIAL_REPORT,
    )

    assert evidence_type == EvidenceType.NEWS


def test_company_report_can_be_financial_report():
    evidence_type, warnings = normalize_evidence_type_for_source(
        source_tier=SourceTier.COMPANY,
        source="company_ir",
        url="https://ir.example.com/annual-report.pdf",
        title="2026 Annual Report",
        content_type="application/pdf",
        current_evidence_type=EvidenceType.UNKNOWN,
    )

    assert evidence_type == EvidenceType.FINANCIAL_REPORT
    assert warnings == []


def test_exchange_pdf_can_be_announcement_or_financial_report():
    evidence_type, _warnings = normalize_evidence_type_for_source(
        source_tier=SourceTier.EXCHANGE_FILING,
        source="cninfo",
        url="https://www.cninfo.com.cn/disclosure/report.pdf",
        title="2026年第一季度报告",
        content_type="application/pdf",
        current_evidence_type=EvidenceType.UNKNOWN,
    )

    assert evidence_type == EvidenceType.FINANCIAL_REPORT


def test_wechat_is_opinion_not_financial_report():
    doc = document_from_hit(
        Hit(
            title="公众号：公司年报点评",
            url="https://mp.weixin.qq.com/s/example",
            snippet="年报点评",
            source="wechat_opencli",
            tier=SourceTier.MEDIA,
            evidence_type=EvidenceType.FINANCIAL_REPORT,
            extra={"content": "公司年报点评，不是官方公告。"},
        )
    )

    assert doc.evidence_type == EvidenceType.OPINION
    assert any("downgraded" in warning for warning in doc.warnings)
