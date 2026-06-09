import os

import pytest

from ir_search import Query, search


@pytest.mark.skipif(os.environ.get("IR_SEARCH_RUN_LIVE_TESTS") != "1", reason="live wechat opencli smoke test disabled")
def test_wechat_opencli_live_smoke():
    result = search(Query(text="公众号 最新文章", sources=["wechat"], count=3))

    assert result.diagnostics[0].source == "wechat_opencli"
    assert result.diagnostics[0].ok is True
    assert result.hits
    assert result.hits[0].title
    assert result.hits[0].url
    assert result.hits[0].extra["platform"] == "wechat"
    assert "account_name" in result.hits[0].extra
