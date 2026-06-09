from __future__ import annotations

from ir_search.adapters.base import AdapterError
from ir_search.models import Query, Hit


class PlaceholderAdapter:
    mode = "placeholder"

    def __init__(self, name: str, message: str) -> None:
        self.name = name
        self.message = message

    def query(self, q: Query) -> list[Hit]:
        raise AdapterError(self.message, retryable=False)
