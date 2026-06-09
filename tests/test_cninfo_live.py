import os

import pytest

from ir_search import Query, search


@pytest.mark.skipif(os.environ.get("IR_SEARCH_RUN_LIVE_TESTS") != "1", reason="live cninfo smoke test disabled")
def test_cninfo_live_smoke():
    result = search(Query(text="中际旭创 最新公告", sources=["cninfo"], count=3))

    assert result.diagnostics[0].source == "cninfo"
    assert result.diagnostics[0].ok is True
    assert result.diagnostics[0].adapter_mode == "live"
    assert result.hits
    assert all(hit.source == "cninfo" for hit in result.hits)
    assert all(hit.tier.name == "EXCHANGE_FILING" for hit in result.hits)
    assert all(hit.published_at is not None for hit in result.hits)
