"""Signed cursors bind keyset positions to a source profile and request."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from threading import Lock

from ir_search.registry import DataAdapterError
from .credentials import credentials_path
from .private_files import _private_read

_KEYS: dict = {}
_KEYS_LOCK = Lock()


def _cursor_key() -> bytes:
    """Persistent installation secret; fail explicitly instead of issuing ephemeral cursors."""
    try:
        root = credentials_path().absolute().parent / ".local" / "state"
        with _KEYS_LOCK:
            if root not in _KEYS:
                _KEYS[root] = _stored_key(root)
            return _KEYS[root]
    except (OSError, ValueError, RuntimeError):
        raise DataAdapterError("cursor_state_unavailable") from None


def _stored_key(root):
    from ir_search.context import RequestContext
    from .private_files import _directory_lock, _private_write
    # Readers also lock: an exclusive empty file is not an atomic publication.
    with _directory_lock(root, RequestContext(timeout_seconds=5)) as directory:
        path = directory / "cursor.key"
        try:
            raw = _private_read(path, 256).strip()
        except FileNotFoundError:
            raw = secrets.token_hex(32).encode("ascii")
            _private_write(path, raw)
        if not re.fullmatch(rb"[0-9a-f]{64}", raw):
            raise ValueError("invalid_cursor_key")
        return bytes.fromhex(raw.decode("ascii"))


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
    except DataAdapterError:
        raise
    except Exception:
        raise DataAdapterError("invalid_cursor") from None
