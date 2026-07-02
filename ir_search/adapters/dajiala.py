from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

from ir_search.adapters.base import AdapterError
from ir_search.adapters.wechat_opencli import rows_to_hits
from ir_search.models import Hit, Query
from tools import gzh_fetch


class DajialaAdapter:
    name = "dajiala"
    mode = "live"

    def query(self, q: Query) -> list[Hit]:
        if not os.environ.get("DAJIALA_KEY"):
            raise AdapterError("DAJIALA_KEY is not set", retryable=False)

        accounts_path = dajiala_accounts_path()
        if not accounts_path.exists():
            raise AdapterError(f"DAJIALA_ACCOUNTS_PATH does not exist: {accounts_path}", retryable=False)

        try:
            account = os.environ.get("DAJIALA_ACCOUNT") or gzh_fetch.infer_account(str(accounts_path), q.text)
            start, end = window_dates(q)
            result = gzh_fetch.run(
                str(accounts_path),
                account,
                start,
                end,
                ["dajiala"],
                want_fulltext=want_fulltext(),
                emit=False,
            )
        except SystemExit as exc:
            raise AdapterError(f"dajiala account inference failed: {exc}", retryable=False) from exc
        except Exception as exc:
            raise AdapterError(f"dajiala fetch failed: {type(exc).__name__}: {exc}", retryable=True) from exc

        rows = gzh_fetch.opencli_rows(result)
        hits = rows_to_hits(rows)
        for hit in hits:
            hit.source = self.name
            hit.found_by = [self.name]
            hit.extra["provider"] = "dajiala"
            hit.extra["provider_only"] = True
            hit.extra["requires_login"] = False
            hit.extra["extraction_method"] = "gzh_dajiala"
        return hits


def dajiala_accounts_path() -> Path:
    configured = os.environ.get("DAJIALA_ACCOUNTS_PATH") or os.environ.get("WECHAT_ACCOUNTS_PATH")
    if configured:
        return Path(configured).expanduser()
    return Path.cwd() / "accounts.json"


def window_dates(q: Query) -> tuple[date, date]:
    if q.window.start and q.window.end:
        return q.window.start.date(), q.window.end.date()
    if q.window.raw == "oneDay":
        return gzh_fetch.default_window(1)
    if q.window.raw == "oneWeek":
        return gzh_fetch.default_window(7)
    if q.window.raw == "oneMonth":
        return gzh_fetch.default_window(30)
    days = int(os.environ.get("DAJIALA_DEFAULT_DAYS", os.environ.get("GZH_FETCH_DEFAULT_DAYS", "14")))
    end = gzh_fetch.datetime.now(gzh_fetch.CST).date()
    start = end - timedelta(days=max(1, days) - 1)
    return start, end


def want_fulltext() -> bool:
    return os.environ.get("DAJIALA_FULLTEXT", "0").strip().lower() in {"1", "true", "yes", "on"}
