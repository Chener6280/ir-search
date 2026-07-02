from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional, Union

from .models import Document, document_from_dict, document_to_dict


class DocumentStore:
    """Small JSON store for fetched documents and research runs."""

    def __init__(self, root: Union[str, Path] = ".ir_search_cache") -> None:
        self.root = Path(root)
        self.documents_dir = self.root / "documents"
        self.research_runs_dir = self.root / "research_runs"
        self.documents_dir.mkdir(parents=True, exist_ok=True)
        self.research_runs_dir.mkdir(parents=True, exist_ok=True)

    def save_document(self, document: Document) -> Path:
        path = self.documents_dir / f"{_safe_filename(document.doc_id)}.json"
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(_scrub_document(document_to_dict(document)), f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
        return path

    def load_document(self, doc_id: str) -> Optional[Document]:
        path = self.documents_dir / f"{_safe_filename(doc_id)}.json"
        if not path.exists():
            return None
        return document_from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save_research_run(self, run_id: str, payload: dict) -> Path:
        path = self.research_runs_dir / f"{_safe_filename(run_id)}.json"
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(_scrub_payload(payload), f, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp_path, path)
        return path


def _safe_filename(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in value)[:180]


def _scrub_document(data: dict) -> dict:
    keep = {
        "doc_id",
        "url",
        "canonical_url",
        "title",
        "source",
        "source_tier",
        "evidence_type",
        "content_type",
        "published_at",
        "fetched_at",
        "extraction_method",
        "text",
        "tables",
        "links",
        "raw_hash",
        "text_hash",
        "warnings",
        "errors",
        "extra",
    }
    scrubbed = {key: value for key, value in data.items() if key in keep}
    scrubbed["extra"] = _scrub_payload(scrubbed.get("extra") or {})
    return scrubbed


def _scrub_payload(payload):
    if isinstance(payload, dict):
        return {
            key: _scrub_payload(value)
            for key, value in payload.items()
            if not _looks_sensitive_key(str(key))
        }
    if isinstance(payload, list):
        return [_scrub_payload(item) for item in payload]
    return payload


def _looks_sensitive_key(key: str) -> bool:
    lower = key.lower()
    return any(needle in lower for needle in ["authorization", "cookie", "token", "api_key", "apikey", "secret"])
