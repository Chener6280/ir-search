from ir_search.adapters.bocha import build_bocha_query, focus_site_domains, sentiment_site_domains
from ir_search.models import Intent, Query


def test_bocha_focus_site_domains_include_requested_media():
    domains = focus_site_domains()

    assert "yicai.com" in domains
    assert "caixin.com" in domains
    assert "caijing.com.cn" in domains
    assert "eeo.com.cn" in domains
    assert "21jingji.com" in domains
    assert "nbd.com.cn" in domains
    assert "jiemian.com" in domains
    assert "cls.cn" in domains
    assert "jwview.com" in domains
    assert "wallstreetcn.com" in domains
    assert "cs.com.cn" in domains
    assert "cnstock.com" in domains
    assert "stcn.com" in domains
    assert "xinhuanet.com" in domains
    assert "ce.cn" in domains


def test_bocha_query_adds_focus_site_clause():
    query = build_bocha_query(Query(text="中际旭创 最新观点", intent=Intent.COMPANY_NEWS))

    assert query.startswith("中际旭创 最新观点")
    assert "site:yicai.com" in query
    assert "site:caixin.com" in query
    assert " OR " in query


def test_bocha_query_keeps_explicit_site_filter():
    query = build_bocha_query(Query(text="中际旭创 site:cninfo.com.cn"))

    assert query == "中际旭创 site:cninfo.com.cn"


def test_bocha_sentiment_query_uses_china_sentiment_sites():
    domains = sentiment_site_domains("cn")
    query = build_bocha_query(Query(text="中际旭创 股吧 热度", intent=Intent.SENTIMENT))

    assert domains == ["guba.eastmoney.com", "10jqka.com.cn", "iwencai.com", "xueqiu.com"]
    assert "site:guba.eastmoney.com" in query
    assert "site:10jqka.com.cn" in query
    assert "site:iwencai.com" in query
    assert "site:xueqiu.com" in query
    assert "site:yicai.com" not in query
