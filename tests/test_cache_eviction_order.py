"""Eviction must remove the oldest write even when the file clock cannot tell writes apart."""
from pathlib import Path

from ir_search.infrastructure.private_files import _oldest_first, _private_write, _stamp_written


def test_burst_writes_keep_wall_time_without_inventing_future_age(tmp_path):
    # Names are deliberately in reverse order of writing: name order must not decide age.
    paths = [tmp_path / f"{99 - index:02d}.json" for index in range(60)]
    import time
    started = time.time_ns()
    for path in paths:
        _private_write(path, b"{}")
    ended = time.time_ns()
    stamps = [path.stat().st_mtime_ns for path in paths]
    assert all(started - 20_000_000 <= stamp <= ended for stamp in stamps)
    assert _oldest_first(paths) == sorted(paths, key=lambda p: (p.stat().st_mtime_ns, p.name))


def test_equal_timestamps_fall_back_to_a_stable_name_order(tmp_path):
    import os
    for name in ("b.json", "a.json", "c.json"):
        (tmp_path / name).write_bytes(b"{}")
        os.utime(tmp_path / name, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    assert [p.name for p in _oldest_first(tmp_path.glob("*.json"))] == ["a.json", "b.json", "c.json"]
    _stamp_written(tmp_path / "a.json")
    assert [p.name for p in _oldest_first(tmp_path.glob("*.json"))][-1] == "a.json"


def test_a_refused_timestamp_update_does_not_fail_a_completed_write(tmp_path, monkeypatch):
    from ir_search.infrastructure import private_files

    def refuse(*args, **kwargs):
        raise PermissionError(13, "file is held by another process")

    monkeypatch.setattr(private_files.os, "utime", refuse)
    _private_write(tmp_path / "kept.json", b'{"ok": true}')
    assert (tmp_path / "kept.json").read_bytes() == b'{"ok": true}'
    assert not list(tmp_path.glob(".write-*"))  # no temporary file is left behind either
