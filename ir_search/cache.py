from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

from .models import EvidenceType, Hit, Query, SourceTier


class FileCache:
    def __init__(self, root: Union[str, Path]) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def get(self, key: str) -> Optional[list[Hit]]:
        path = self.root / f"{_safe_key(key)}.json"
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as f:
            return [_hit_from_dict(item) for item in json.load(f)]

    def set(self, key: str, hits: list[Hit]) -> None:
        path = self.root / f"{_safe_key(key)}.json"
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump([_hit_to_dict(hit) for hit in hits], f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)


class CallLogger:
    def __init__(self, path: Union[str, Path]) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, record: dict) -> None:
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def cache_key(source: str, q: Query) -> str:
    bucket = utc_now().strftime("%Y-%m-%d")
    return f"{source}|{q.text}|{q.window.raw}|{q.count}|{bucket}"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_key(key: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in key)[:180]


def _hit_to_dict(hit: Hit) -> dict:
    data = asdict(hit)
    data["tier"] = hit.tier.name
    data["evidence_type"] = hit.evidence_type.value
    data["published_at"] = hit.published_at.isoformat() if hit.published_at else None
    data["fetched_at"] = hit.fetched_at.isoformat() if hit.fetched_at else None
    return data


def _hit_from_dict(data: dict) -> Hit:
    for field in ["published_at", "fetched_at"]:
        if data.get(field):
            data[field] = datetime.fromisoformat(data[field])
    data["tier"] = SourceTier[data["tier"]]
    data["evidence_type"] = EvidenceType(data["evidence_type"])
    return Hit(**data)
