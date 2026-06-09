from __future__ import annotations

from typing import Protocol

from ir_search.models import Hit, Query


class AdapterError(Exception):
    def __init__(self, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class SearchAdapter(Protocol):
    name: str
    mode: str

    def query(self, q: Query) -> list[Hit]:
        ...
