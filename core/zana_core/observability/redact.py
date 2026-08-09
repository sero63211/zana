"""Observability redaction adapters.

The single generic recursive redactor lives in ``zana_core.streaming.redaction``.
This module adapts it for the observability surface and adds the exact trusted
``Event`` path, which never calls ``model_dump``, ``hasattr``, ``str``, or
``repr`` on hostile values.
"""

from __future__ import annotations

from datetime import UTC, datetime
from math import isfinite
from typing import Any

from zana_core.observability.events import (
    MAX_DURATION_MS,
    MAX_SCHEMA_VERSION,
    MAX_STRING_BYTES,
    MAX_STRING_LENGTH,
    Event,
    EventContext,
    EventKind,
    Severity,
    payload_to_builtin,
)
from zana_core.streaming.redaction import (
    DEFAULT_REDACTION_LIMITS,
    REDACTED,
    RedactionLimits,
    Redactor,
    is_sensitive_key,
    redact_value,
)

__all__ = [
    "DEFAULT_REDACTION_LIMITS",
    "REDACTED",
    "RedactionLimits",
    "RedactedValue",
    "Redactor",
    "is_sensitive_key",
    "redact_event",
    "redact_object",
    "redact_value",
]


class RedactedValue:
    """Compatibility marker; canonical redaction emits ``REDACTED`` strings."""


def redact_object(value: Any) -> Any:
    """Redact an exact built-in value using the canonical bounded redactor."""
    return redact_value(value)


def _require_str(value: Any, max_length: int = MAX_STRING_LENGTH) -> str:
    if type(value) is not str:
        raise ValueError("event field must be an exact string")
    if len(value) > max_length:
        raise ValueError("event field exceeds its length bound")
    return value


def _require_utf8_str(value: Any, max_length: int, max_bytes: int) -> str:
    text = _require_str(value, max_length)
    if len(text.encode("utf-8", errors="replace")) > max_bytes:
        raise ValueError("event field exceeds its UTF-8 byte bound")
    return text


def _require_identifier(value: Any, max_length: int = 128) -> str:
    text = _require_str(value, max_length)
    if any(ord(character) < 32 or ord(character) == 127 for character in text):
        raise ValueError("event identifier contains control characters")
    return text


def _require_int_range(value: Any, minimum: int, maximum: int) -> int:
    if type(value) is not int or value < minimum or value > maximum:
        raise ValueError("event field violates its integer bound")
    return value


def _require_optional_int_range(value: Any, minimum: int, maximum: int) -> int | None:
    return None if value is None else _require_int_range(value, minimum, maximum)


def _require_float_range(value: Any, minimum: float, maximum: float) -> float:
    if type(value) is not float or not isfinite(value):
        raise ValueError("event field must be a finite exact float")
    if value < minimum or value > maximum:
        raise ValueError("event field violates its float bound")
    return value


def _require_optional_float_range(value: Any, minimum: float, maximum: float) -> float | None:
    return None if value is None else _require_float_range(value, minimum, maximum)


def _require_optional_identifier(value: Any, max_length: int = 128) -> str | None:
    return None if value is None else _require_identifier(value, max_length)


def _require_enum(value: Any, enum_type: type[Any]) -> str:
    if type(value) is not enum_type:
        raise ValueError("event enum field has the wrong exact type")
    text = value.value
    if type(text) is not str or len(text) > 64:
        raise ValueError("event enum field violates its string bound")
    return text


def _require_utc_timestamp(value: Any) -> str:
    if type(value) is not datetime:
        raise ValueError("event timestamp must be an exact datetime")
    try:
        tzinfo = value.tzinfo
    except AttributeError:
        raise ValueError("event timestamp timezone is missing") from None
    if tzinfo is not UTC:
        raise ValueError("event timestamp must use the exact UTC timezone singleton")
    return value.isoformat()


def redact_event(value: Any) -> dict[str, Any]:
    """Redact one exact trusted ``Event`` without raw object handling."""
    if type(value) is not Event:
        raise TypeError("redact_event requires an exact Event")
    try:
        context = value.context
        if type(context) is not EventContext:
            raise ValueError("event context is not exact")
        payload = payload_to_builtin(value.payload)
        raw: dict[str, Any] = {
            "schema_version": _require_int_range(value.schema_version, 1, MAX_SCHEMA_VERSION),
            "kind": _require_enum(value.kind, EventKind),
            "severity": _require_enum(value.severity, Severity),
            "message": _require_utf8_str(value.message, MAX_STRING_LENGTH, MAX_STRING_BYTES),
            "timestamp": _require_utc_timestamp(value.timestamp),
            "context": {
                "operation_id": _require_identifier(context.operation_id),
                "job_id": _require_identifier(context.job_id),
                "phase": _require_identifier(context.phase, 64),
                "instance_id": _require_optional_identifier(context.instance_id),
                "image_digest": _require_optional_identifier(context.image_digest),
            },
            "operation_id": _require_identifier(value.operation_id),
            "job_id": _require_identifier(value.job_id),
            "phase": _require_identifier(value.phase, 64),
            "progress_0_1": _require_optional_float_range(value.progress_0_1, 0.0, 1.0),
            "duration_ms": _require_optional_int_range(value.duration_ms, 0, MAX_DURATION_MS),
            "recovery_code": _require_optional_identifier(value.recovery_code, 64),
            "payload": payload,
        }
        return redact_value(raw, DEFAULT_REDACTION_LIMITS)
    except (ValueError, AttributeError, TypeError):
        raise ValueError("event payload or fields violate bounds") from None
