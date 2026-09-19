"""Shared bounded JSON-RPC response validation; no provider credentials or I/O."""
import json
from ir_search.registry import DataAdapterError


def _reject_constant(value):
    raise ValueError("Invalid JSON constant")


def _decode(raw):
    return json.loads(raw, parse_constant=_reject_constant)


def _response_for(value, request_id):
    messages = value if isinstance(value, list) else [value]
    if not messages or len(messages) > 32:
        raise DataAdapterError("upstream_schema")
    matching = []
    for message in messages:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise DataAdapterError("upstream_schema")
        if "method" in message:
            if "id" in message:
                # We advertise no sampling/elicitation capabilities and never execute server requests.
                raise DataAdapterError("unsupported")
            continue
        if type(message.get("id")) is type(request_id) and message["id"] == request_id:
            matching.append(message)
        else:
            raise DataAdapterError("upstream_schema")
    if len(matching) > 1:
        raise DataAdapterError("upstream_schema")
    return matching[0] if matching else None
