"""Pagination cursors are signed with an installation key, never with a credential."""
import hashlib
import hmac
from datetime import date

import pytest

from ir_search import DataRequest
from ir_search.infrastructure import pagination
from ir_search.infrastructure.credentials import MySQLProfile
from ir_search.registry import DataAdapterError

PROFILE = MySQLProfile("wind_mysql", "db.example.test", "wind", "reader", "must_not_escape-password")
REQUEST = DataRequest("prices_daily", symbols=("600519.SH",), start=date(2026, 1, 5), end=date(2026, 1, 9))


def test_cursor_round_trips_and_is_not_verifiable_with_the_database_password():
    token = pagination._encode_cursor(REQUEST, PROFILE, ["600519.SH", "2026-01-07"])
    assert pagination._decode_cursor(REQUEST.__class__(**{**REQUEST.__dict__, "cursor": token}), PROFILE) == ["600519.SH", "2026-01-07"]
    data, signature = token.split(".")
    import base64
    raw = base64.b64decode(data, altchars=b"-_", validate=True)
    assert hmac.new(PROFILE.password.encode(), raw, hashlib.sha256).hexdigest() != signature
    assert "must_not_escape" not in token


def test_key_is_random_persistent_and_private(tmp_path, monkeypatch):
    monkeypatch.setenv("IR_SEARCH_CREDENTIALS_FILE", str(tmp_path / "credentials.env"))
    monkeypatch.setattr(pagination, "_KEYS", {})
    first = pagination._cursor_key()
    stored = tmp_path / ".local" / "state" / "cursor.key"
    assert len(first) == 32 and bytes.fromhex(stored.read_text(encoding="ascii")) == first
    monkeypatch.setattr(pagination, "_KEYS", {})  # a new process reads the same key
    assert pagination._cursor_key() == first
    monkeypatch.setenv("IR_SEARCH_CREDENTIALS_FILE", str(tmp_path / "other" / "credentials.env"))
    assert pagination._cursor_key() != first  # another installation, another key


def test_unusable_state_location_falls_back_to_a_process_key(tmp_path, monkeypatch):
    blocker = tmp_path / ".local"
    blocker.write_text("not a directory", encoding="ascii")
    monkeypatch.setenv("IR_SEARCH_CREDENTIALS_FILE", str(tmp_path / "credentials.env"))
    monkeypatch.setattr(pagination, "_KEYS", {})
    key = pagination._cursor_key()
    assert len(key) == 32 and pagination._cursor_key() == key


def test_tampered_or_foreign_cursor_is_rejected(monkeypatch):
    token = pagination._encode_cursor(REQUEST, PROFILE, ["600519.SH", "2026-01-07"])
    with_cursor = lambda value: REQUEST.__class__(**{**REQUEST.__dict__, "cursor": value})
    data, signature = token.split(".")
    with pytest.raises(DataAdapterError, match="invalid_cursor"):
        pagination._decode_cursor(with_cursor(data + "." + "0" * 64), PROFILE)
    monkeypatch.setattr(pagination, "_KEYS", {None: b"k" * 32})
    monkeypatch.setattr(pagination, "credentials_path", lambda: (_ for _ in ()).throw(OSError()))
    with pytest.raises(DataAdapterError, match="invalid_cursor"):
        pagination._decode_cursor(with_cursor(token), PROFILE)
