"""Bounded recursive redaction of secret-bearing values."""

from __future__ import annotations

import re
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


class RedactionLimits(BaseModel):
    """Bounds for recursive redaction."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_depth: int = Field(default=24, gt=0)
    max_items: int = Field(default=10_000, gt=0)
    max_string_length: int = Field(default=2048, gt=0)


DEFAULT_REDACTION_LIMITS = RedactionLimits()

_SENSITIVE_NORMALIZED: frozenset[str] = frozenset(
    {
        "authorization",
        "authtoken",
        "accesstoken",
        "apitoken",
        "apikey",
        "xapikey",
        "cookie",
        "cookies",
        "credential",
        "credentials",
        "password",
        "passwd",
        "secret",
        "secrets",
        "privatekey",
        "accesskey",
        "sessionkey",
        "clientsecret",
        "refreshtoken",
        "bearer",
        "token",
        "tokens",
    }
)

_NORMALIZE_RE = re.compile(r"[^a-z0-9]")

REDACTED = "***"
TRUNCATED_SUFFIX = "...[truncated]"


def _normalize_key(key: str) -> str:
    return _NORMALIZE_RE.sub("", key.lower())


def is_sensitive_key(key: str) -> bool:
    """Whether a mapping key may carry a secret value."""
    return _normalize_key(key) in _SENSITIVE_NORMALIZED


def truncate_safe_string(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return value[: max(0, max_length - len(TRUNCATED_SUFFIX))] + TRUNCATED_SUFFIX


class RedactionProvider(Protocol):
    """Protocol for event payload redaction."""

    def redact(self, value: Any) -> Any: ...


class Redactor(RedactionProvider):
    """Recursively remove secret values while preserving canonical safe fields."""

    def __init__(self, limits: RedactionLimits | None = None) -> None:
        self.limits = limits or DEFAULT_REDACTION_LIMITS

    def redact(self, value: Any) -> Any:
        return _redact_value(
            value,
            depth=0,
            items=0,
            limits=self.limits,
        )


def redact_value(
    value: Any,
    limits: RedactionLimits | None = None,
) -> Any:
    """Redact a value under bounded depth/items/string length."""
    return _redact_value(
        value,
        depth=0,
        items=0,
        limits=limits or DEFAULT_REDACTION_LIMITS,
    )


def _redact_value(value: Any, *, depth: int, items: int, limits: RedactionLimits) -> Any:
    if depth >= limits.max_depth:
        return REDACTED
    if items >= limits.max_items:
        return REDACTED
    if isinstance(value, dict):
        if items >= limits.max_items:
            return REDACTED
        result: dict[str, Any] = {}
        for key, child in value.items():
            if items >= limits.max_items:
                result[str(key)] = REDACTED
                continue
            items += 1
            if is_sensitive_key(str(key)):
                result[str(key)] = REDACTED
                continue
            result[str(key)] = _redact_value(
                child,
                depth=depth + 1,
                items=items,
                limits=limits,
            )
        return result
    if isinstance(value, list):
        result_list: list[Any] = []
        for child in value:
            if items >= limits.max_items:
                result_list.append(REDACTED)
                continue
            items += 1
            result_list.append(
                _redact_value(
                    child,
                    depth=depth + 1,
                    items=items,
                    limits=limits,
                )
            )
        return result_list
    if isinstance(value, tuple):
        return tuple(
            _redact_value(
                child,
                depth=depth + 1,
                items=items + 1,
                limits=limits,
            )
            for child in value
        )
    if isinstance(value, str):
        return truncate_safe_string(value, limits.max_string_length)
    return value
