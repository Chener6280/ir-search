import json
import os
import time

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


def test_cache_prune_removes_old_files(tmp_path):
    cache = FileCache(tmp_path)
    cache.set("old", [Hit(title="old", url="https://old", snippet="s", source="test")])
    cache.set("fresh", [Hit(title="fresh", url="https://fresh", snippet="s", source="test")])
    old_file = next(path for path in tmp_path.glob("*.json") if "old" in path.name)
    old_time = time.time() - 3 * 24 * 60 * 60
    os.utime(old_file, (old_time, old_time))

    assert cache.prune(days=1) == 1
    assert len(list(tmp_path.glob("*.json"))) == 1
