"""Bounded sanitization for persisted and client-facing acquisition state."""

from __future__ import annotations

from typing import Any

from zana_core.acquisition.limits import (
    MAX_ERROR_CODE_LENGTH,
    MAX_MODEL_REFERENCE_BYTES,
)

MAX_TEXT_CHARS = 256
MAX_TEXT_BYTES = 1024
MAX_CODE_CHARS = 64
_INVALID = "...[invalid-text]"


def bounded_text(value: str, *, max_chars: int = MAX_TEXT_CHARS) -> str:
    """Return a byte-bounded sanitized string or a fixed invalid marker."""
    if type(value) is not str:
        return _INVALID
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return _INVALID
    if len(value) > max_chars:
        value = value[:max_chars]
    encoded = value.encode("utf-8")
    if len(encoded) <= MAX_TEXT_BYTES:
        return value
    truncated = encoded[:MAX_TEXT_BYTES]
    while truncated:
        try:
            return truncated.decode("utf-8")
        except UnicodeDecodeError:
            truncated = truncated[:-1]
    return _INVALID


def bounded_code(value: str) -> str:
    """Return a bounded canonical error code or a fixed marker."""
    if type(value) is not str:
        return "UNKNOWN"
    if len(value) > MAX_CODE_CHARS or len(value.encode("utf-8")) > MAX_ERROR_CODE_LENGTH:
        return "UNKNOWN"
    return value


def sanitize_model_reference(value: str) -> str:
    """Validate a model reference without ever persisting endpoint data."""
    if type(value) is not str:
        raise ValueError("model_reference must be a string")
    value = value.strip()
    if not value or value in {".", ".."}:
        raise ValueError("model_reference must be a bounded non-empty reference")
    if len(value) > MAX_MODEL_REFERENCE_BYTES:
        raise ValueError("model_reference exceeds the byte limit")
    if len(value.encode("utf-8")) > MAX_MODEL_REFERENCE_BYTES:
        raise ValueError("model_reference exceeds the UTF-8 byte limit")
    if "\x00" in value or "\n" in value or "\r" in value:
        raise ValueError("model_reference contains forbidden control bytes")
    if any(ord(char) < 32 for char in value):
        raise ValueError("model_reference contains forbidden control characters")
    return value


def sanitize_job_payload(
    *,
    runtime_id: int,
    model_reference: str,
    expected_size_bytes: int | None,
    user_approved: bool,
    deadline_seconds: float,
    runtime_kind: str,
    runtime_source: str,
    runtime_status: str,
    runtime_identity: str,
) -> dict[str, Any]:
    """Build the only persisted acquisition state; no endpoints or secrets."""
    if type(runtime_id) is not int or runtime_id <= 0:
        raise ValueError("runtime_id must be a positive int")
    reference = sanitize_model_reference(model_reference)
    if expected_size_bytes is not None:
        if type(expected_size_bytes) is not int:
            raise ValueError("expected_size_bytes must be an int")
        if expected_size_bytes <= 0 or expected_size_bytes > (1 << 40):
            raise ValueError("expected_size_bytes is out of range")
    if type(user_approved) is not bool:
        raise ValueError("user_approved must be a bool")
    if type(deadline_seconds) not in (int, float):
        raise ValueError("deadline_seconds must be numeric")
    deadline = float(deadline_seconds)
    if not (0 < deadline <= 3600):
        raise ValueError("deadline_seconds is out of range")
    return {
        "code": "ACQUISITION_QUEUED",
        "message": "Native model acquisition queued.",
        "runtime_id": runtime_id,
        "runtime_kind": bounded_text(runtime_kind, max_chars=24),
        "runtime_source": bounded_text(runtime_source, max_chars=16),
        "runtime_status": bounded_text(runtime_status, max_chars=16),
        "runtime_identity": bounded_text(runtime_identity, max_chars=64),
        "model_reference": reference,
        "expected_size_bytes": expected_size_bytes,
        "user_approved": user_approved,
        "deadline_seconds": deadline,
    }


def sanitize_terminal_error(
    *,
    code: str,
    message: str,
    actions: list[str] | None = None,
) -> dict[str, Any]:
    """Build a bounded recoverable error payload without raw details."""
    clean_actions = []
    for action in actions or ():
        clean = bounded_text(action, max_chars=64)
        if clean and clean not in clean_actions:
            clean_actions.append(clean)
    return {
        "code": bounded_code(code),
        "message": bounded_text(message),
        "recoverable": True,
        "actions": clean_actions[:8],
    }
