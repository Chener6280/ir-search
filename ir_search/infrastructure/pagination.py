"""Signed cursors bind keyset positions to a source profile and request."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json

from ir_search.registry import DataAdapterError


def _binding(request, profile):
    value = request.to_dict()
    value.pop("cursor", None)
    value.pop("provider", None)
    value.update(provider=profile.provider, database=profile.database, host=profile.host, user=profile.user)
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _encode_cursor(request, profile, last):
    data = json.dumps({"v": 1, "request": _binding(request, profile), "last": last}, separators=(",", ":")).encode()
    signature = hmac.new(profile.password.encode(), data, hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(data).decode() + "." + signature


def _decode_cursor(request, profile):
    if not request.cursor:
        return None
    try:
        encoded, signature = request.cursor.split(".")
        data = base64.b64decode(encoded, altchars=b"-_", validate=True)
        expected = hmac.new(profile.password.encode(), data, hashlib.sha256).hexdigest()
        payload = json.loads(data)
        if not hmac.compare_digest(signature, expected) or payload["v"] != 1 or payload["request"] != _binding(request, profile):
            raise ValueError()
        return payload["last"]
    except Exception:
        raise DataAdapterError("invalid_cursor") from None
