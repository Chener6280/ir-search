from ir_search.cache import FileCache, _safe_key, cache_key
from ir_search.kernel import search
from ir_search.models import Intent, Query


def test_cache_records_adapter_hits(tmp_path):
    cache = FileCache(tmp_path)
    q = Query(text="中际旭创 一季报", sources=["cninfo"])

    first = search(q, cache=cache)
    second = search(q, cache=cache)

    assert first.diagnostics[0].cache_hit is False
    assert second.diagnostics[0].cache_hit is True
    assert len(second.hits) > 0


def test_cache_key_includes_intent_and_source_mode():
    base = Query(text="同一问题", sources=["bocha"], intent=Intent.COMPANY_NEWS)
    other_intent = Query(text="同一问题", sources=["bocha"], intent=Intent.POLICY)
    auto_sources = Query(text="同一问题", sources=None, intent=Intent.COMPANY_NEWS)

    assert cache_key("bocha", base) != cache_key("bocha", other_intent)
    assert cache_key("bocha", base) != cache_key("bocha", auto_sources)


def test_safe_key_keeps_hash_for_long_keys():
    left = "x" * 220 + "left"
    right = "x" * 220 + "right"

    assert _safe_key(left) != _safe_key(right)
    assert len(_safe_key(left)) <= 157
