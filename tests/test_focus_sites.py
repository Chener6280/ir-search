from ir_search.adapters.bocha import build_bocha_query
from ir_search.models import Intent, Query


def test_no_media_focus_for_filing():
    query = build_bocha_query(Query(text="中际旭创 最新公告", intent=Intent.FILING))

    assert "site:yicai.com" not in query
    assert "site:caixin.com" not in query
    assert "site:cninfo.com.cn" in query


def test_policy_uses_regulator_focus_sites():
    query = build_bocha_query(Query(text="出口管制 半导体", intent=Intent.POLICY))

    assert "site:mofcom.gov.cn" in query
    assert "site:miit.gov.cn" in query
    assert "site:yicai.com" not in query


def test_news_uses_media_focus_sites():
    query = build_bocha_query(Query(text="中际旭创 最新观点", intent=Intent.COMPANY_NEWS))

    assert "site:yicai.com" in query
    assert "site:caixin.com" in query


def test_existing_site_query_not_modified():
    query = build_bocha_query(Query(text="中际旭创 site:cninfo.com.cn", intent=Intent.COMPANY_NEWS))

    assert query == "中际旭创 site:cninfo.com.cn"


def test_general_query_no_focus_by_default():
    query = build_bocha_query(Query(text="中际旭创", intent=Intent.GENERAL))

    assert query == "中际旭创"
