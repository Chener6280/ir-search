"""First use of a lock file by several threads at once must still serialize them."""
import os
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from ir_search import RequestContext
from ir_search.infrastructure import private_files
from ir_search.infrastructure.private_files import _directory_lock


def test_threads_racing_to_create_the_lock_file_never_overlap(tmp_path):
    active, overlaps, entered = [0], [0], [0]
    guard = threading.Lock()

    def work(_):
        with _directory_lock(tmp_path / "state", RequestContext(timeout_seconds=20)):
            with guard:
                active[0] += 1; entered[0] += 1
                overlaps[0] += active[0] > 1
            time.sleep(0.01)
            with guard:
                active[0] -= 1

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(work, range(8)))
    assert entered[0] == 8 and overlaps[0] == 0


@pytest.mark.skipif(os.name != "nt", reason="the first-byte write exists only in the Windows byte-range lock")
def test_refused_first_byte_write_is_contention_not_failure(tmp_path, monkeypatch):
    real_write = os.write

    def refuse_initial_byte(fd, data):
        if data == b"0":
            raise PermissionError(13, "Permission denied")  # another holder has locked the byte
        return real_write(fd, data)

    monkeypatch.setattr(private_files.os, "write", refuse_initial_byte)
    with _directory_lock(tmp_path / "state", RequestContext(timeout_seconds=5)) as root:
        assert root.is_dir()
