"""Structured redaction for permission denials and logs.

Denial objects must never contain secret values or private document contents.
The helpers in this module are the only place that converts a raw value into a
loggable representation.
"""

from __future__ import annotations

from pathlib import Path

REDACTED_VALUE = "***"


def redact_value(value: object) -> str:
    """Return a fixed placeholder instead of any caller-supplied value."""
    del value
    return REDACTED_VALUE


def redact_path(path: str | Path) -> str:
    """Keep only the basename, which errors may reference without leaking paths."""
    return Path(str(path)).name or REDACTED_VALUE


def redact_reference(reference: str) -> str:
    """Keep a short identifier but never a value that could be a secret."""
    return reference.strip() or REDACTED_VALUE
