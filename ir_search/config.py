from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


PACKAGE_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=None)
def load_yaml(name: str) -> dict[str, Any]:
    path = PACKAGE_DIR / "configs" / name
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
