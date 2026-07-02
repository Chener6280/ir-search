from ir_search.documents.models import Document, make_doc_id, utc_now
from ir_search.evidence import extract_evidence
from ir_search.models import EvidenceType, SourceTier


def test_extract_evidence_returns_relevant_stable_span():
    text = """无关段落：公司历史沿革和办公地址。

中际旭创 一季报 显示海外 AI 光模块需求保持强劲，800G 产品收入增长。

风险提示：交付节奏仍可能受客户资本开支影响。"""
    doc = Document(
        doc_id=make_doc_id("https://example.com/a", "hash"),
        url="https://example.com/a",
        canonical_url="https://example.com/a",
        title="中际旭创 一季报",
        source="cninfo",
        source_tier=SourceTier.EXCHANGE_FILING,
        evidence_type=EvidenceType.FINANCIAL_REPORT,
        content_type="html",
        published_at=None,
        fetched_at=utc_now(),
        extraction_method="fixture",
        text=text,
    )

    spans = extract_evidence(doc, "海外 AI 光模块需求", max_spans=2)
    repeat = extract_evidence(doc, "海外 AI 光模块需求", max_spans=2)

    assert spans
    assert "海外 AI 光模块需求保持强劲" in spans[0].text
    assert spans[0].span_id == repeat[0].span_id
    assert spans[0].doc_id == doc.doc_id
    assert spans[0].url == doc.url


def test_extract_evidence_returns_empty_without_overlap():
    doc = Document(
        doc_id=make_doc_id("https://example.com/a", "hash"),
        url="https://example.com/a",
        canonical_url="https://example.com/a",
        title="无关",
        source="web",
        source_tier=SourceTier.MEDIA,
        evidence_type=EvidenceType.NEWS,
        content_type="html",
        published_at=None,
        fetched_at=utc_now(),
        extraction_method="fixture",
        text="这是一段完全无关的天气描述。",
    )

    assert extract_evidence(doc, "海外 AI 光模块需求") == []
