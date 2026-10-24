"""Archival export contracts: collection listings, timeline records and comments.

These types describe metadata-level, keyword-free enumeration for external
archivers. They carry upstream metadata only; credentials, signed URLs and
original bytes never appear in these structures.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from . import JsonModel, _instant, _text
from .materials import _reference


def _optional_text(value, name: str, maximum: int = 1000) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"Invalid {name}")
    return value


def _metadata_dicts(values, name: str) -> tuple:
    if not isinstance(values, (list, tuple)) or len(values) > 50:
        raise ValueError(f"Invalid {name}")
    for value in values:
        if not isinstance(value, dict):
            raise ValueError(f"Invalid {name}")
    return tuple(values)


@dataclass(frozen=True)
class CollectionDescriptor(JsonModel):
    """One account-visible collection: a group, knowledge base or static category."""
    collection_id: str
    name: Optional[str] = None
    category: Optional[str] = None
    role: Optional[str] = None
    status: Optional[str] = None

    def __post_init__(self):
        _text(self.collection_id, "collection_id")
        _optional_text(self.name, "collection name")
        _optional_text(self.category, "collection category", 100)
        _optional_text(self.role, "collection role", 100)
        if self.status is not None and (not isinstance(self.status, (str, int)) or isinstance(self.status, bool)):
            raise ValueError("Invalid collection status")
        if isinstance(self.status, str) and not self.status.strip():
            raise ValueError("Invalid collection status")


@dataclass(frozen=True)
class TimelineItem(JsonModel):
    """One metadata-level record returned by a keyword-free timeline export."""
    ref: str
    title: str
    type: str
    collection_id: str
    published_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    author: Optional[str] = None
    authors: tuple = ()
    labels: tuple = ()
    like_count: Optional[int] = None
    comments_count: Optional[int] = None
    attachments: tuple = ()
    images: tuple = ()
    text: Optional[str] = None
    text_scope: Optional[str] = None
    summary: Optional[str] = None
    has_summary: Optional[bool] = None
    created_at: Optional[datetime] = None
    modified_at: Optional[datetime] = None
    original_document_available: Optional[bool] = None
    category: Optional[str] = None
    original_url: Optional[str] = None
    warnings: tuple = ()

    def __post_init__(self):
        _reference(self.ref)
        _text(self.title, "title")
        _text(self.type, "record type")
        _text(self.collection_id, "collection_id")
        for name in ("published_at", "updated_at", "created_at", "modified_at"):
            object.__setattr__(self, name, _instant(getattr(self, name)))
        _optional_text(self.author, "author")
        if not isinstance(self.authors, (list, tuple)) or len(self.authors) > 50:
            raise ValueError("Invalid authors")
        for value in self.authors:
            _optional_text(value, "author")
        object.__setattr__(self, "authors", tuple(self.authors))
        if not isinstance(self.labels, (list, tuple)) or len(self.labels) > 50:
            raise ValueError("Invalid labels")
        for value in self.labels:
            _optional_text(value, "label", 200)
        object.__setattr__(self, "labels", tuple(self.labels))
        for name in ("like_count", "comments_count"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"Invalid {name}")
        object.__setattr__(self, "attachments", _metadata_dicts(self.attachments, "attachments"))
        object.__setattr__(self, "images", _metadata_dicts(self.images, "images"))
        if self.text is not None and (not isinstance(self.text, str) or len(self.text) > 100000):
            raise ValueError("Invalid record text")
        _optional_text(self.text_scope, "text scope", 100)
        if self.summary is not None and (not isinstance(self.summary, str) or len(self.summary) > 100000):
            raise ValueError("Invalid record summary")
        if self.has_summary is not None and type(self.has_summary) is not bool:
            raise ValueError("Invalid summary flag")
        if self.original_document_available is not None and type(self.original_document_available) is not bool:
            raise ValueError("Invalid original-document flag")
        _optional_text(self.category, "category", 100)
        if self.original_url is not None:
            _reference(self.original_url, web_only=True)
        if not isinstance(self.warnings, (list, tuple)) or len(self.warnings) > 50:
            raise ValueError("Invalid warnings")
        for value in self.warnings:
            _optional_text(value, "warning", 200)
        object.__setattr__(self, "warnings", tuple(self.warnings))


@dataclass(frozen=True)
class TimelineComment(JsonModel):
    """One attributed comment on an exported topic, preserving nested replies."""
    ref: str
    comment_id: str
    text: str
    author: Optional[str] = None
    role: Optional[str] = None
    created_at: Optional[datetime] = None
    reply_to: Optional[dict] = None
    replies: tuple = ()
    like_count: Optional[int] = None
    warnings: tuple = ()

    def __post_init__(self):
        _reference(self.ref)
        _text(self.comment_id, "comment id")
        if not isinstance(self.text, str) or len(self.text) > 100000:
            raise ValueError("Invalid comment text")
        _optional_text(self.author, "author")
        _optional_text(self.role, "role", 100)
        object.__setattr__(self, "created_at", _instant(self.created_at))
        if self.reply_to is not None and not isinstance(self.reply_to, dict):
            raise ValueError("Invalid reply target")
        if not isinstance(self.replies, (list, tuple)) or len(self.replies) > 50:
            raise ValueError("Invalid nested replies")
        for value in self.replies:
            if not isinstance(value, TimelineComment):
                raise ValueError("Invalid nested replies")
        object.__setattr__(self, "replies", tuple(self.replies))
        if self.like_count is not None and (type(self.like_count) is not int or self.like_count < 0):
            raise ValueError("Invalid like count")
        if not isinstance(self.warnings, (list, tuple)) or len(self.warnings) > 50:
            raise ValueError("Invalid warnings")
        for value in self.warnings:
            _optional_text(value, "warning", 200)
        object.__setattr__(self, "warnings", tuple(self.warnings))
