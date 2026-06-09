from ir_search.cache import FileCache
from ir_search.kernel import search
from ir_search.models import Query


def test_cache_records_adapter_hits(tmp_path):
    cache = FileCache(tmp_path)
    q = Query(text="中际旭创 一季报", sources=["cninfo"])

    first = search(q, cache=cache)
    second = search(q, cache=cache)

    assert first.diagnostics[0].cache_hit is False
    assert second.diagnostics[0].cache_hit is True
    assert len(second.hits) > 0
