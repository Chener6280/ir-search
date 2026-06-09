import json
import threading

from ir_search.cache import CallLogger


def test_call_logger_writes_jsonl(tmp_path):
    logger = CallLogger(tmp_path / "calls.jsonl")
    logger.write({"a": 1})

    assert json.loads((tmp_path / "calls.jsonl").read_text(encoding="utf-8")) == {"a": 1}


def test_call_logger_concurrent_writes_are_valid_jsonl(tmp_path):
    logger = CallLogger(tmp_path / "calls.jsonl")

    threads = [threading.Thread(target=logger.write, args=({"i": i},)) for i in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    rows = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 20
    assert sorted(row["i"] for row in rows) == list(range(20))
