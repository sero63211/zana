"""Single canonical bounded recursive redactor for ZANA.

Observability and streaming both use this implementation.  It is generic,
dependency-free, and fails closed: secrets and private content never leave,
unknown objects are never dumped, and every traversal stops at a shared
mutable budget.

The redactor accepts only exact built-in primitives and exact ``dict`` /
``list`` / ``tuple`` containers.  Mappings, iterables, paths, bytes, sets,
models, and other objects become ``REDACTED`` without calling ``__str__``,
``__repr__``, ``hasattr``, ``model_dump``, iteration, or mapping hooks.
Cycles and repeated mutable-container aliases are replaced by ``REDACTED``.

Key collision behavior is deterministic: non-string keys and keys that exceed
the key bounds collapse to the fixed ``<redacted-key>`` marker with value
``REDACTED``; later collapsed entries overwrite earlier ones.  When an item or
output-byte budget is exhausted, one final ``<redacted-key>`` entry (or
``REDACTED`` list item) is appended and traversal stops, so a result may exceed
the configured cap by exactly that one marker.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

REDACTED = "***"
TRUNCATED_SUFFIX = "...[truncated]"
MAX_SAFE_PATH_BASENAME = 128
MAX_PATH_INPUT_BYTES = 4096
_PATH_DIGEST_PREFIX_CHARS = 512
_MIN_EXACT_INT = -(2**63)
_MAX_EXACT_INT = 2**63 - 1
_REDACTED_KEY_MARKER = "<redacted-key>"

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

_CONTENT_NORMALIZED: frozenset[str] = frozenset(
    {
        "prompt",
        "response",
        "completion",
        "document",
        "documentcontent",
        "content",
        "raw",
        "rawbody",
        "requestbody",
        "responsebody",
        "body",
        "environment",
        "env",
    }
)

_SAFE_OPERATIONAL_NORMALIZED: frozenset[str] = frozenset(
    {
        "status",
        "message",
        "recoverycode",
        "errorcode",
        "operationid",
        "jobid",
        "phase",
        "progress01",
        "durationms",
        "count",
        "digest",
        "basename",
        "ok",
    }
)

_PATH_KEY_NORMALIZED: frozenset[str] = frozenset(
    {
        "path",
        "file",
        "filename",
        "filepath",
        "directory",
        "root",
        "source",
        "destination",
        "sourcepath",
        "destinationpath",
        "localpath",
        "logroot",
        "logrootpath",
        "workspace",
        "workspacepath",
    }
)

_SAFE_STRING_KEY_NORMALIZED: frozenset[str] = frozenset(
    {"status", "message", "recoverycode", "errorcode", "operationid", "jobid", "phase"}
)

_NORMALIZE_RE = re.compile(r"[^a-z0-9]")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_PATH_SPLIT_RE = re.compile(r"[\\/]+")


class RedactionLimits(BaseModel):
    """Bounds for one recursive redaction call."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", allow_inf_nan=False)

    max_depth: int = Field(default=24, gt=0, le=64)
    max_items: int = Field(default=512, gt=0, le=4096)
    max_container_items: int = Field(default=128, gt=0, le=1024)
    max_string_length: int = Field(default=512, gt=0, le=2048)
    max_string_bytes: int = Field(default=1024, gt=0, le=4096)
    max_key_length: int = Field(default=128, gt=0, le=256)
    max_key_bytes: int = Field(default=256, gt=0, le=512)
    max_output_bytes: int = Field(default=7168, gt=0, le=16384)


DEFAULT_REDACTION_LIMITS = RedactionLimits()

_LIMIT_FIELDS: tuple[tuple[str, int, int], ...] = (
    ("max_depth", 1, 64),
    ("max_items", 1, 4096),
    ("max_container_items", 1, 1024),
    ("max_string_length", 1, 2048),
    ("max_string_bytes", 1, 4096),
    ("max_key_length", 1, 256),
    ("max_key_bytes", 1, 512),
    ("max_output_bytes", 1, 16384),
)


def _trusted_limits(limits: RedactionLimits) -> RedactionLimits:
    """Revalidate an exact limits instance into a fresh trusted copy.

    Pydantic's ``model_construct`` and ``object.__setattr__`` can create exact
    ``RedactionLimits`` instances with hostile or out-of-range fields, so every
    public consumption rechecks each field without instance-dispatched
    ``model_dump``/copy hooks and returns a fresh immutable value.
    """
    if type(limits) is not RedactionLimits:
        raise TypeError("limits must be an exact RedactionLimits or None")
    fields: dict[str, int] = {}
    for name, minimum, maximum in _LIMIT_FIELDS:
        try:
            value = object.__getattribute__(limits, name)
        except (AttributeError, TypeError):
            raise ValueError(f"limits field {name} is missing") from None
        if type(value) is not int or value < minimum or value > maximum:
            raise ValueError(f"limits field {name} violates bounds")
        fields[name] = value
    try:
        return RedactionLimits(**fields)
    except ValueError:
        raise ValueError("limits fields violate bounds") from None


def _json_string_bytes(value: str) -> int:
    """Exact UTF-8 length of one canonical JSON string value."""
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def canonical_json_byte_length(value: Any) -> int:
    """Exact compact-JSON byte length for an exact built-in value.

    This helper is intentionally limited to the exact types the redactor can
    emit, so the budget tracks the final canonical JSON representation
    (``separators=(",", ":")``, ``ensure_ascii=False``).
    """
    if value is None:
        return 4
    if type(value) is bool:
        return 4 if value else 5
    if type(value) is int:
        if value < _MIN_EXACT_INT or value > _MAX_EXACT_INT:
            raise ValueError("int exceeds exact canonical bound")
        return len(str(value))
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("non-finite float")
        return len(json.dumps(value, allow_nan=False))
    if type(value) is str:
        return _json_string_bytes(value)
    if type(value) is dict:
        if not value:
            return 2
        total = 2
        size = len(value)
        for index, (item_key, item_value) in enumerate(value.items()):
            total += (
                canonical_json_byte_length(item_key) + 1 + canonical_json_byte_length(item_value)
            )
            if index < size - 1:
                total += 1
        return total
    if type(value) in (list, tuple):
        if not value:
            return 2
        total = 2
        size = len(value)
        for index, item in enumerate(value):
            total += canonical_json_byte_length(item)
            if index < size - 1:
                total += 1
        return total
    raise ValueError("unsupported redacted value")


class _Budget:
    """Shared mutable item and output-byte budget for one redaction call."""

    __slots__ = ("items", "output_bytes", "max_items", "max_output_bytes", "exhausted")

    def __init__(self, max_items: int, max_output_bytes: int) -> None:
        self.items = max_items
        self.output_bytes = 0
        self.max_items = max_items
        self.max_output_bytes = max_output_bytes
        self.exhausted = False

    def spend_item(self) -> bool:
        if self.exhausted:
            return False
        if self.items <= 0:
            self.exhausted = True
            return False
        self.items -= 1
        return True

    def charge(self, amount: int) -> bool:
        if self.exhausted:
            return False
        if self.output_bytes + amount > self.max_output_bytes:
            self.exhausted = True
            return False
        self.output_bytes += amount
        return True

    def refund(self, amount: int) -> None:
        self.output_bytes = max(0, self.output_bytes - amount)


def _normalize_key(key: str) -> str:
    return _NORMALIZE_RE.sub("", key.lower())


def is_sensitive_key(key: str) -> bool:
    """Whether an exact string mapping key may carry a secret or private value."""
    if type(key) is not str:
        return False
    normalized = _normalize_key(key)
    return normalized in _SENSITIVE_NORMALIZED or (
        normalized in _CONTENT_NORMALIZED and normalized not in _SAFE_OPERATIONAL_NORMALIZED
    )


def is_path_key(key: str) -> bool:
    """Whether an exact string mapping key carries a private filesystem path."""
    if type(key) is not str:
        return False
    normalized = _normalize_key(key)
    return normalized in _PATH_KEY_NORMALIZED and normalized not in _SAFE_STRING_KEY_NORMALIZED


def _path_digest(value: str, length: int) -> str:
    """Stable bounded digest over a bounded prefix and the exact caller length.

    The caller has already gated the character length, so digesting a fixed
    prefix plus the exact length never scans or encodes the whole path string.
    """
    prefix = value[:_PATH_DIGEST_PREFIX_CHARS]
    digest = hashlib.sha256(prefix.encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"path-{digest}-len{length}"


def _safe_path_value(value: str) -> str:
    """Reduce a path string to a bounded basename or a stable digest.

    Parsing is pure string handling so Windows paths are redacted the same way
    on every host.  The character length is gated before any encoding, and only
    a bounded prefix is ever inspected or encoded for the digest marker.
    """
    if not value:
        return _path_digest("", 0)
    length = len(value)
    if length > MAX_PATH_INPUT_BYTES:
        prefix_end = MAX_PATH_INPUT_BYTES
        if ord(value[prefix_end - 1]) >= 0x80:
            while prefix_end > 0 and (ord(value[prefix_end - 1]) & 0xC0 == 0x80):
                prefix_end -= 1
            if prefix_end > 0:
                first = ord(value[prefix_end - 1])
                if (
                    ((first & 0xE0) == 0xC0 and prefix_end < 2)
                    or ((first & 0xF0) == 0xE0 and prefix_end < 3)
                    or ((first & 0xF8) == 0xF0 and prefix_end < 4)
                ):
                    prefix_end = 0
        return _path_digest(value[:prefix_end], length)
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return _path_digest(value, length)
    if "/" not in value and "\\" not in value:
        if _DRIVE_RE.match(value):
            return _path_digest(value, length)
        if len(value) <= MAX_SAFE_PATH_BASENAME and value not in (".", ".."):
            return value
        return _path_digest(value, length)
    parts = [part for part in _PATH_SPLIT_RE.split(value) if part]
    if not parts:
        return _path_digest(value, length)
    basename = parts[-1]
    if basename in (".", "..") or len(basename.encode("utf-8")) > MAX_SAFE_PATH_BASENAME:
        return _path_digest(value, length)
    return basename


def truncate_safe_string(
    value: str,
    max_length: int | RedactionLimits | None = None,
    *,
    limits: RedactionLimits | None = None,
) -> str:
    """Truncate a safe string to bounded UTF-8 bytes.

    ``max_length`` preserves the canonical streaming integer API as a
    character bound; ``limits`` accepts the bounded observability limits.  The
    two bound sources are mutually exclusive and exact-type checked.
    """
    if limits is not None:
        if max_length is not None:
            raise TypeError("provide either max_length or limits, not both")
        selected = _trusted_limits(limits)
    elif max_length is None:
        selected = _trusted_limits(DEFAULT_REDACTION_LIMITS)
    elif type(max_length) is int:
        if max_length < 1 or max_length > 2048:
            raise ValueError("max_length must be within the canonical string bound")
        selected = _trusted_limits(RedactionLimits(max_string_length=max_length))
    elif type(max_length) is RedactionLimits:
        selected = _trusted_limits(max_length)
    else:
        raise TypeError("max_length must be an exact int, RedactionLimits, or None")
    return _truncate_safe_string_trusted(value, selected)


def _truncate_safe_string_trusted(value: str, bounds: RedactionLimits) -> str:
    """Truncate using an already revalidated trusted limits instance."""
    if type(value) is not str:
        return REDACTED
    if not value:
        return value
    if (
        len(value) <= bounds.max_string_length
        and len(value.encode("utf-8", errors="replace")) <= bounds.max_string_bytes
    ):
        return value
    suffix_bytes = len(TRUNCATED_SUFFIX.encode("utf-8"))
    budget_chars = bounds.max_string_length - len(TRUNCATED_SUFFIX)
    if budget_chars <= 0:
        return REDACTED
    safe = value[:budget_chars]
    budget_bytes = bounds.max_string_bytes - suffix_bytes
    if budget_bytes <= 0:
        return REDACTED
    while safe and len(safe.encode("utf-8", errors="replace")) > budget_bytes:
        safe = safe[:-1]
    return safe + TRUNCATED_SUFFIX


class RedactionProvider(Protocol):
    """Protocol for event payload redaction."""

    def redact(self, value: Any) -> Any: ...


class Redactor(RedactionProvider):
    """Recursively remove secrets while preserving canonical safe fields."""

    def __init__(self, limits: RedactionLimits | None = None) -> None:
        if limits is not None and type(limits) is not RedactionLimits:
            raise TypeError("limits must be an exact RedactionLimits or None")
        self.limits = _trusted_limits(DEFAULT_REDACTION_LIMITS if limits is None else limits)

    def redact(self, value: Any) -> Any:
        return redact_value(value, self.limits)


def redact_value(
    value: Any,
    limits: RedactionLimits | None = None,
) -> Any:
    """Redact a value under one shared bounded traversal."""
    if limits is not None and type(limits) is not RedactionLimits:
        raise TypeError("limits must be an exact RedactionLimits or None")
    bounds = _trusted_limits(DEFAULT_REDACTION_LIMITS if limits is None else limits)
    budget = _Budget(bounds.max_items, bounds.max_output_bytes)
    return _redact_value(
        value,
        depth=0,
        budget=budget,
        seen=set(),
        limits=bounds,
    )


def _append_truncation_marker(result: dict[str, Any]) -> None:
    result[_REDACTED_KEY_MARKER] = REDACTED


def _redact_value(
    value: Any,
    *,
    depth: int,
    budget: _Budget,
    seen: set[int],
    limits: RedactionLimits,
    key: str | None = None,
) -> Any:
    if not budget.spend_item():
        return REDACTED
    if depth >= limits.max_depth:
        return REDACTED
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if value < _MIN_EXACT_INT or value > _MAX_EXACT_INT:
            return REDACTED
        return value
    if type(value) is float:
        if not math.isfinite(value):
            return REDACTED
        return value
    if type(value) is str:
        if key is not None and is_sensitive_key(key):
            return REDACTED
        if key is not None and is_path_key(key):
            return _safe_path_value(value)
        return _truncate_safe_string_trusted(value, limits)
    if type(value) is dict:
        return _redact_mapping(
            value,
            depth=depth,
            budget=budget,
            seen=seen,
            limits=limits,
        )
    if type(value) in (list, tuple):
        return _redact_sequence(
            value,
            depth=depth,
            budget=budget,
            seen=seen,
            limits=limits,
        )
    return REDACTED


def _redact_mapping(
    value: dict[str, Any],
    *,
    depth: int,
    budget: _Budget,
    seen: set[int],
    limits: RedactionLimits,
) -> dict[str, Any]:
    identity = id(value)
    if identity in seen:
        return {_REDACTED_KEY_MARKER: REDACTED}
    seen.add(identity)
    result: dict[str, Any] = {}
    if not budget.charge(2):
        _append_truncation_marker(result)
        return result
    for item_key, child in value.items():
        if len(result) >= limits.max_container_items:
            _append_truncation_marker(result)
            break
        if not budget.spend_item():
            _append_truncation_marker(result)
            break
        if type(item_key) is not str:
            _append_truncation_marker(result)
            continue
        if item_key == _REDACTED_KEY_MARKER:
            _append_truncation_marker(result)
            continue
        if (
            len(item_key) > limits.max_key_length
            or len(item_key.encode("utf-8")) > limits.max_key_bytes
        ):
            _append_truncation_marker(result)
            continue
        if is_sensitive_key(item_key):
            redacted_child: Any = REDACTED
        elif is_path_key(item_key):
            redacted_child = _safe_path_value(child) if type(child) is str else REDACTED
        else:
            redacted_child = _redact_value(
                child,
                depth=depth + 1,
                budget=budget,
                seen=seen,
                limits=limits,
                key=item_key,
            )
        key_cost = _json_string_bytes(item_key) + 1
        child_cost = canonical_json_byte_length(redacted_child)
        if not budget.charge(key_cost + child_cost + 1):
            _append_truncation_marker(result)
            break
        result[item_key] = redacted_child
    if result:
        budget.refund(1)
    return result


def _redact_sequence(
    value: list[Any] | tuple[Any, ...],
    *,
    depth: int,
    budget: _Budget,
    seen: set[int],
    limits: RedactionLimits,
) -> list[Any]:
    track_identity = type(value) is list
    identity = id(value) if track_identity else -1
    if track_identity and identity in seen:
        return [REDACTED]
    if track_identity:
        seen.add(identity)
    result: list[Any] = []
    if not budget.charge(2):
        result.append(REDACTED)
        return result
    for child in value:
        if len(result) >= limits.max_container_items:
            result.append(REDACTED)
            break
        if not budget.spend_item():
            result.append(REDACTED)
            break
        redacted_child = _redact_value(
            child,
            depth=depth + 1,
            budget=budget,
            seen=seen,
            limits=limits,
        )
        if not budget.charge(canonical_json_byte_length(redacted_child) + 1):
            result.append(REDACTED)
            break
        result.append(redacted_child)
    if result:
        budget.refund(1)
    return result
