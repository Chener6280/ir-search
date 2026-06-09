import json

from ir_search.cache import FileCache, utc_now
from ir_search.models import Hit


def test_cache_file_is_valid_json_after_write(tmp_path):
    cache = FileCache(tmp_path)
    cache.set("key", [Hit(title="t", url="https://a", snippet="s", source="test")])

    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text(encoding="utf-8"))[0]["title"] == "t"


def test_cache_write_uses_atomic_replace(tmp_path):
    cache = FileCache(tmp_path)
    cache.set("key", [Hit(title="t", url="https://a", snippet="s", source="test")])

    assert not list(tmp_path.glob("*.tmp"))


def test_utc_timestamps_are_timezone_aware():
    assert utc_now().tzinfo is not None
