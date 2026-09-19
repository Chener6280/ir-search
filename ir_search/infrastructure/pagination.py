"""Signed cursors bind keyset positions to a source profile and request."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
from threading import Lock

from ir_search.registry import DataAdapterError
from .credentials import credentials_path
from .private_files import _private_dir, _private_read

_KEYS: dict = {}
_KEYS_LOCK = Lock()


def _cursor_key() -> bytes:
    """A random per-installation signing key, never derived from a credential.

    Cursors travel to callers, model context, logs and shared test reports. Signing them
    with the database password would give every reader a way to test password guesses
    offline. The key lives beside the private credentials file; where that location cannot
    hold private state the key is process-local and cursors end with the process.
    """
    try:
        root = credentials_path().absolute().parent / ".local" / "state"
    except OSError:
        root = None
    with _KEYS_LOCK:
        if root not in _KEYS:
            _KEYS[root] = _stored_key(root) or secrets.token_bytes(32)
        return _KEYS[root]


def _stored_key(root):
    if root is None:
        return None
    try:
        path = _private_dir(root) / "cursor.key"
        for _ in range(2):
            try:
                raw = _private_read(path, 256).strip()
                return bytes.fromhex(raw.decode("ascii")) if re.fullmatch(rb"[0-9a-f]{64}", raw) else None
            except FileNotFoundError:
                pass
            try:  # Exclusive create: a concurrent process keeps its key and this one re-reads it.
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            except FileExistsError:
                continue
            with os.fdopen(fd, "wb") as stream:
                stream.write(secrets.token_hex(32).encode("ascii"))
    except (OSError, ValueError):
        pass
    return None


def _binding(request, profile):
    value = request.to_dict()
    value.pop("cursor", None)
    value.pop("provider", None)
    value.update(provider=profile.provider, database=profile.database, host=profile.host, user=profile.user)
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _encode_cursor(request, profile, last):
    data = json.dumps({"v": 1, "request": _binding(request, profile), "last": last}, separators=(",", ":")).encode()
    signature = hmac.new(_cursor_key(), data, hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(data).decode() + "." + signature


def _decode_cursor(request, profile):
    if not request.cursor:
        return None
    try:
        encoded, signature = request.cursor.split(".")
        data = base64.b64decode(encoded, altchars=b"-_", validate=True)
        expected = hmac.new(_cursor_key(), data, hashlib.sha256).hexdigest()
        payload = json.loads(data)
        if not hmac.compare_digest(signature, expected) or payload["v"] != 1 or payload["request"] != _binding(request, profile):
            raise ValueError()
        return payload["last"]
    except Exception:
        raise DataAdapterError("invalid_cursor") from None
