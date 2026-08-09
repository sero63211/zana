"""Deterministic canonical JSON Lines serialization with size bounds."""

from __future__ import annotations

import json
from typing import Any

from zana_core.observability.events import Event
from zana_core.observability.redact import redact_event

MAX_ENCODED_LINE_BYTES = 8192


class SerializationFailureError(Exception):
    """Raised when an event cannot be serialized within bounds."""


def _encode_redacted_raw(redacted: dict[str, Any]) -> str:
    return json.dumps(
        redacted,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _dropped_record() -> str:
    """Fixed, literal fallback record with no caller-controlled fields."""
    dropped = {
        "schema_version": 1,
        "kind": "system",
        "severity": "warning",
        "message": "event dropped: serialization bound exceeded",
        "operation_id": "",
        "job_id": "",
        "phase": "",
        "recovery_code": "EVENT_OVERSIZE",
    }
    return (
        json.dumps(
            dropped,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    )


def serialize_event(event: Event) -> str:
    """Serialize one exact ``Event``, always enforcing the encoded line bound."""
    if type(event) is not Event:
        raise TypeError("serialize_event requires an exact Event")
    try:
        redacted = redact_event(event)
        if type(redacted) is not dict:
            raise SerializationFailureError("redacted event must be a mapping")
        candidate = _encode_redacted_raw(redacted)
        if len(candidate.encode("utf-8")) > MAX_ENCODED_LINE_BYTES:
            return _dropped_record()
        return candidate + "\n"
    except Exception:
        return _dropped_record()


class JsonLinesCodec:
    """Deterministic JSON Lines codec with stable schema version."""

    def encode(self, event: Event) -> str:
        return serialize_event(event)

    def encode_redacted(self, event: Event) -> dict[str, Any]:
        """Return the canonical redacted mapping without a huge intermediate line."""
        if type(event) is not Event:
            raise TypeError("encode_redacted requires an exact Event")
        try:
            redacted = redact_event(event)
            if type(redacted) is not dict:
                raise SerializationFailureError("redacted event must be a mapping")
            return redacted
        except ValueError:
            raise SerializationFailureError("event cannot be redacted within bounds") from None

    def encoded_bytes(self, event: Event) -> int:
        return len(self.encode(event).encode("utf-8"))
