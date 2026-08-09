"""Canonical bounded SSE encoder emitting one UTF-8 bytes chunk at a time."""

from __future__ import annotations

import json
import re
from typing import Any

from zana_core.streaming.models import (
    ErrorMetadata,
    StreamEvent,
    StreamLimits,
)
from zana_core.streaming.redaction import RedactionProvider

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class StreamEncodeError(ValueError):
    """Raised when an event cannot be encoded safely."""


class StreamLimitError(StreamEncodeError):
    """Raised before a configured stream cap is exceeded."""


def canonical_json_bytes(value: Any) -> bytes:
    """Deterministic compact JSON with sorted keys."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _validate_text(
    value: str,
    *,
    label: str,
    max_chars: int,
    allow_newline: bool = False,
) -> str:
    if len(value) > max_chars:
        raise StreamLimitError(f"{label} exceeds {max_chars} characters")
    if not allow_newline and any(character in value for character in "\r\n\x00"):
        raise StreamEncodeError(f"{label} contains forbidden control characters")
    if _CONTROL_RE.search(value):
        raise StreamEncodeError(f"{label} contains control characters")
    return value


def _sanitize_event_id(event: StreamEvent, limits: StreamLimits) -> str | None:
    if event.id is None:
        return None
    return _validate_text(
        event.id,
        label="event id",
        max_chars=limits.max_identifier_chars,
    )


def _sanitize_name(event: StreamEvent, limits: StreamLimits) -> str:
    name = event.name.value if hasattr(event.name, "value") else str(event.name)
    return _validate_text(name, label="event name", max_chars=limits.max_name_chars)


class SSEEncoder:
    """Encode events one UTF-8 bytes chunk at a time with strict caps."""

    def __init__(
        self,
        limits: StreamLimits | None = None,
        *,
        redactor: RedactionProvider | None = None,
    ) -> None:
        self.limits = limits or StreamLimits()
        self.redactor = redactor
        self.total_bytes = 0

    def encode(self, event: StreamEvent) -> bytes:
        """Encode one event and return its complete wire chunk."""
        chunk = self._encode_event(event)
        if len(chunk) > self.limits.max_event_bytes:
            raise StreamLimitError(f"event exceeds {self.limits.max_event_bytes} bytes")
        if self.total_bytes + len(chunk) > self.limits.max_total_bytes:
            raise StreamLimitError("total stream would exceed the configured byte cap")
        self.total_bytes += len(chunk)
        return chunk

    def encode_keepalive(self, comment: str) -> bytes:
        """Encode an explicit caller-supplied keepalive comment."""
        if not isinstance(comment, str) or not comment:
            raise StreamEncodeError("keepalive comment must be a non-empty string")
        _validate_text(
            comment,
            label="keepalive comment",
            max_chars=self.limits.max_identifier_chars,
        )
        chunk = f": {comment}\n\n".encode()
        if self.total_bytes + len(chunk) > self.limits.max_total_bytes:
            raise StreamLimitError("total stream would exceed the configured byte cap")
        self.total_bytes += len(chunk)
        return chunk

    def _encode_event(self, event: StreamEvent) -> bytes:
        event_id = _sanitize_event_id(event, self.limits)
        name = _sanitize_name(event, self.limits)
        retry = event.retry_ms
        if retry is not None:
            if retry > self.limits.max_retry_ms:
                raise StreamLimitError(f"retry exceeds {self.limits.max_retry_ms} ms")
            retry_text = str(retry)
        else:
            retry_text = None

        data = event.data
        if isinstance(data, BaseException):
            raise StreamEncodeError("raw exceptions/tracebacks must never be serialized")
        if self.redactor is not None:
            data = self.redactor.redact(data)
        try:
            data_bytes = canonical_json_bytes(data)
        except (TypeError, ValueError) as error:
            raise StreamEncodeError(
                "event data is not JSON serializable; raw objects are rejected"
            ) from error
        if len(data_bytes) > self.limits.max_data_bytes:
            raise StreamLimitError(f"event data exceeds {self.limits.max_data_bytes} bytes")

        lines: list[str] = []
        if event_id is not None:
            lines.append(f"id: {event_id}")
        lines.append(f"event: {name}")
        if retry_text is not None:
            lines.append(f"retry: {retry_text}")
        # SSE data may contain newlines; split into canonical data: lines.
        decoded = data_bytes.decode("utf-8")
        for line in decoded.split("\n"):
            lines.append(f"data: {line}")
        if event.error is not None:
            error_lines = canonical_json_bytes(_error_payload(event.error)).decode("utf-8")
            for line in error_lines.split("\n"):
                lines.append(f"data: {line}")
        if event.terminal:
            lines.append("data: [DONE]")
        lines.append("")
        lines.append("")
        return "\n".join(lines).encode("utf-8")


def encode_keepalive_comment(
    comment: str,
    limits: StreamLimits | None = None,
) -> bytes:
    """Standalone explicit keepalive encoder; never auto-timed."""
    return SSEEncoder(limits).encode_keepalive(comment)


def _error_payload(error: ErrorMetadata) -> dict[str, Any]:
    return {
        "error": {
            "code": error.code,
            "message": error.message,
            "recoverable": error.recoverable,
            "recovery_action": error.recovery_action,
            "terminal": error.terminal,
        }
    }
