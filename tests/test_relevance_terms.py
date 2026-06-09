from ir_search.models import Query
from ir_search.pipeline import prepare_query
from ir_search.rerank import extract_query_terms, relevance_score
from ir_search.models import Hit


def test_extract_chinese_terms():
    q = prepare_query(Query(text="中际旭创 光模块 最新"))

    terms = extract_query_terms(q)
    assert "中际旭创" in terms
    assert "光模块" in terms
    assert "最新" not in terms


def test_extract_security_code():
    q = prepare_query(Query(text="300308 最新公告"))

    assert "300308" in extract_query_terms(q)


def test_expanded_terms_are_used():
    q = prepare_query(Query(text="中际旭创"))

    assert "800G" in extract_query_terms(q)


def test_stopwords_are_downweighted_or_removed():
    q = Query(text="最新 情况 影响")

    assert extract_query_terms(q) == []


def test_relevance_does_not_depend_on_full_sentence_only():
    q = prepare_query(Query(text="中际旭创 光模块 最新"))
    hit = Hit(title="中际旭创 800G 光模块订单", url="https://a", snippet="需求强劲", source="test")

    assert relevance_score(q, hit) > 0.35
