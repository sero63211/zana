"""Strict immutable structured event models.

Event payloads are validated and deeply frozen before Pydantic materializes
them.  The retained mapping is a private exact tuple-backed ``_FrozenDict``
with no writable attributes, so backing state cannot be reassigned or mutated
through the mapping object.  Every trusted conversion first runs one bounded
validation pass over the exact JSON grammar and aggregate byte budget, so
invalid instances created by bypassing ``_FrozenDict.__new__`` fail closed
before any recursive materialization.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

MAX_STRING_LENGTH = 512
MAX_STRING_BYTES = 1024
MAX_MAP_SIZE = 64
MAX_LIST_SIZE = 128
MAX_DEPTH = 8
MAX_AGGREGATE_BYTES = 6144
MAX_SCHEMA_VERSION = 1024
MAX_DURATION_MS = 31_536_000_000
MIN_PAYLOAD_INT = -(2**63)
MAX_PAYLOAD_INT = 2**63 - 1

_IDENTIFIER_LIMITS = {
    "operation_id": 128,
    "job_id": 128,
    "phase": 64,
    "instance_id": 128,
    "image_digest": 128,
    "recovery_code": 64,
}


def utc_now() -> datetime:
    return datetime.now(UTC)


class Severity(str, Enum):
    """Event severity."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class EventKind(str, Enum):
    """Canonical event kinds."""

    SYSTEM = "system"
    JOB = "job"
    RUNTIME = "runtime"
    BUILD = "build"
    TOOL = "tool"
    INSTANCE = "instance"
    EVALUATION = "evaluation"


class _StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", allow_inf_nan=False)


def _validate_identifier(value: str, max_length: int) -> str:
    if type(value) is not str:
        raise ValueError("identifier must be an exact string")
    if len(value) > max_length:
        raise ValueError("identifier exceeds its length bound")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("identifier contains control characters")
    return value


def _is_frozen_mapping(value: Any) -> bool:
    """Exact-type check for the immutable trusted mapping used by Event."""
    return type(value) is _FrozenDict


def _primitive_json_bytes(value: Any) -> int:
    if value is None:
        return 4
    if type(value) is bool:
        return 4 if value else 5
    if type(value) is int:
        if value < MIN_PAYLOAD_INT or value > MAX_PAYLOAD_INT:
            raise ValueError("payload integer exceeds signed 64-bit bound")
        return len(str(value))
    if type(value) is float:
        return len(json.dumps(value, allow_nan=False))
    if type(value) is str:
        return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    raise ValueError("unsupported payload primitive")


def _frozen_grammar_bytes(value: Any, *, depth: int, seen: set[int]) -> int:
    """Validate exact bounded JSON grammar and return exact compact JSON bytes.

    This is the single trusted validation pass.  It uses only exact type
    checks and raw tuple iteration, never calling methods on arbitrary
    mappings, iterables, or objects.  Container identities remain in ``seen``
    for the whole traversal, so cycles and repeated mutable/container aliases
    are rejected.
    """
    if depth > MAX_DEPTH:
        raise ValueError("payload exceeds depth bound")
    if value is None or type(value) is bool:
        return _primitive_json_bytes(value)
    if type(value) is int:
        return _primitive_json_bytes(value)
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("payload contains a non-finite float")
        return _primitive_json_bytes(value)
    if type(value) is str:
        if len(value) > MAX_STRING_LENGTH:
            raise ValueError("payload string exceeds length bound")
        if len(value.encode("utf-8", errors="replace")) > MAX_STRING_BYTES:
            raise ValueError("payload string exceeds UTF-8 byte bound")
        return _primitive_json_bytes(value)
    if type(value) is tuple:
        identity = id(value)
        if identity in seen:
            raise ValueError("payload contains a cycle or repeated mutable alias")
        seen.add(identity)
        if len(value) > MAX_LIST_SIZE:
            raise ValueError("payload list exceeds item bound")
        total = 2
        size = len(value)
        for index, item in enumerate(tuple.__iter__(value)):
            total += _frozen_grammar_bytes(item, depth=depth + 1, seen=seen)
            if index < size - 1:
                total += 1
        return total
    if _is_frozen_mapping(value):
        identity = id(value)
        if identity in seen:
            raise ValueError("payload contains a cycle or repeated mutable alias")
        seen.add(identity)
        if len(value) > MAX_MAP_SIZE:
            raise ValueError("payload mapping exceeds item bound")
        total = 2
        size = len(value)
        keys: set[str] = set()
        for index, item in enumerate(tuple.__iter__(value)):
            if type(item) is not tuple or len(item) != 2:
                raise ValueError("event payload item must be an exact key/value pair")
            item_key, item_value = item
            if type(item_key) is not str:
                raise ValueError("event payload keys must be exact strings")
            if len(item_key) > MAX_STRING_LENGTH:
                raise ValueError("payload key exceeds length bound")
            if len(item_key.encode("utf-8", errors="replace")) > MAX_STRING_BYTES:
                raise ValueError("payload key exceeds UTF-8 byte bound")
            if item_key in keys:
                raise ValueError("payload keys must be unique")
            keys.add(item_key)
            total += (
                _primitive_json_bytes(item_key)
                + 1
                + _frozen_grammar_bytes(item_value, depth=depth + 1, seen=seen)
            )
            if index < size - 1:
                total += 1
        if total > MAX_AGGREGATE_BYTES:
            raise ValueError("payload exceeds aggregate byte bound")
        return total
    raise ValueError("payload must contain only exact built-in JSON values")


class _FrozenDict(tuple[tuple[str, Any], ...]):
    """Private exact tuple-backed immutable trusted mapping.

    Instances are exact ``_FrozenDict`` only, hold their pairs directly in the
    immutable tuple, and expose no writable attributes (``__slots__ = ()`` and
    no ``__dict__``).  Normal construction validates key bounds, map bounds,
    duplicates, grammar, and aggregate bytes.  Because ``tuple.__new__`` can
    bypass this, every conversion path also runs ``_frozen_grammar_bytes``
    before materializing values.
    """

    __slots__ = ()

    def __new__(
        cls,
        items: list[tuple[str, Any]] | tuple[tuple[str, Any], ...],
    ) -> _FrozenDict:
        if type(items) not in (list, tuple):
            raise ValueError("event payload must be an exact list or tuple")
        if len(items) > MAX_MAP_SIZE:
            raise ValueError("payload mapping exceeds item bound")
        pairs: list[tuple[str, Any]] = []
        keys: set[str] = set()
        seen: set[int] = set()
        for item in items:
            if type(item) is not tuple or len(item) != 2:
                raise ValueError("event payload item must be an exact key/value pair")
            item_key, item_value = item
            if type(item_key) is not str:
                raise ValueError("event payload keys must be exact strings")
            if len(item_key) > MAX_STRING_LENGTH:
                raise ValueError("payload key exceeds length bound")
            if len(item_key.encode("utf-8", errors="replace")) > MAX_STRING_BYTES:
                raise ValueError("payload key exceeds UTF-8 byte bound")
            if item_key in keys:
                raise ValueError("payload keys must be unique")
            keys.add(item_key)
            _frozen_grammar_bytes(item_value, depth=0, seen=seen)
            pairs.append((item_key, item_value))
        instance = super().__new__(cls, tuple(pairs))
        _frozen_grammar_bytes(instance, depth=0, seen=set())
        return instance

    def __getitem__(self, key: Any) -> Any:
        if type(key) is str:
            for item_key, item_value in tuple.__iter__(self):
                if item_key == key:
                    return item_value
            raise KeyError(key)
        if type(key) in (int, slice):
            return super().__getitem__(key)
        raise TypeError("payload key must be an exact string, int, or slice")

    def __iter__(self):
        return (item_key for item_key, _ in tuple.__iter__(self))

    def __len__(self) -> int:
        return tuple.__len__(self)

    def __contains__(self, key: object) -> bool:
        if type(key) is not str:
            return False
        return any(item_key == key for item_key, _ in tuple.__iter__(self))

    def items(self) -> tuple[tuple[str, Any], ...]:
        return tuple.__getitem__(self, slice(None))

    def keys(self) -> tuple[str, ...]:
        return tuple(item_key for item_key, _ in tuple.__iter__(self))

    def values(self) -> tuple[Any, ...]:
        return tuple(item_value for _, item_value in tuple.__iter__(self))

    def __eq__(self, other: object) -> bool:
        try:
            _frozen_grammar_bytes(self, depth=0, seen=set())
            if type(other) is dict:
                frozen_other = _freeze_payload(other)
                return _builtin_payload_value(self) == _builtin_payload_value(frozen_other)
            if type(other) is _FrozenDict:
                _frozen_grammar_bytes(other, depth=0, seen=set())
                return _builtin_payload_value(self) == _builtin_payload_value(other)
            return False
        except ValueError:
            return False

    def __hash__(self) -> int:
        _frozen_grammar_bytes(self, depth=0, seen=set())
        return tuple.__hash__(self)


def _make_frozen_mapping(items: list[tuple[str, Any]]) -> _FrozenDict:
    return _FrozenDict(items)


def _freeze_value(value: Any, *, depth: int, seen: set[int]) -> Any:
    if depth > MAX_DEPTH:
        raise ValueError("payload exceeds depth bound")
    if _is_frozen_mapping(value):
        _frozen_grammar_bytes(value, depth=depth, seen=set())
        identity = id(value)
        if identity in seen:
            raise ValueError("payload contains a cycle or repeated mutable alias")
        seen.add(identity)
        items = [
            (item_key, _freeze_value(item_value, depth=depth + 1, seen=seen))
            for item_key, item_value in tuple.__iter__(value)
        ]
        return _make_frozen_mapping(items)
    if type(value) is tuple:
        identity = id(value)
        if identity in seen:
            raise ValueError("payload contains a cycle or repeated mutable alias")
        seen.add(identity)
        if len(value) > MAX_LIST_SIZE:
            raise ValueError("payload list exceeds item bound")
        return tuple(_freeze_value(item, depth=depth + 1, seen=seen) for item in value)
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if value < MIN_PAYLOAD_INT or value > MAX_PAYLOAD_INT:
            raise ValueError("payload integer exceeds signed 64-bit bound")
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("payload contains a non-finite float")
        return value
    if type(value) is str:
        if len(value) > MAX_STRING_LENGTH:
            raise ValueError("payload string exceeds length bound")
        if len(value.encode("utf-8", errors="replace")) > MAX_STRING_BYTES:
            raise ValueError("payload string exceeds UTF-8 byte bound")
        return value
    if type(value) is dict:
        identity = id(value)
        if identity in seen:
            raise ValueError("payload contains a cycle or repeated mutable alias")
        seen.add(identity)
        if len(value) > MAX_MAP_SIZE:
            raise ValueError("payload mapping exceeds item bound")
        items: list[tuple[str, Any]] = []
        for item_key, item_value in value.items():
            if type(item_key) is not str:
                raise ValueError("payload keys must be exact strings")
            if len(item_key) > MAX_STRING_LENGTH:
                raise ValueError("payload key exceeds length bound")
            if len(item_key.encode("utf-8", errors="replace")) > MAX_STRING_BYTES:
                raise ValueError("payload key exceeds UTF-8 byte bound")
            items.append((item_key, _freeze_value(item_value, depth=depth + 1, seen=seen)))
        return _make_frozen_mapping(items)
    if type(value) is list:
        identity = id(value)
        if identity in seen:
            raise ValueError("payload contains a cycle or repeated mutable alias")
        seen.add(identity)
        if len(value) > MAX_LIST_SIZE:
            raise ValueError("payload list exceeds item bound")
        return tuple(_freeze_value(item, depth=depth + 1, seen=seen) for item in value)
    raise ValueError("payload must contain only exact built-in JSON values")


def _freeze_payload(value: Any) -> _FrozenDict:
    if type(value) is dict:
        root: Any = value
    elif _is_frozen_mapping(value):
        _frozen_grammar_bytes(value, depth=0, seen=set())
        root = _builtin_payload_value(value)
    else:
        raise ValueError("event payload must be an exact dict")
    frozen = _freeze_value(root, depth=0, seen=set())
    if not _is_frozen_mapping(frozen):
        raise ValueError("event payload root must be a mapping")
    _frozen_grammar_bytes(frozen, depth=0, seen=set())
    return frozen


def validate_payload(value: Any) -> dict[str, Any]:
    """Validate and freeze a raw payload, returning a fresh plain top-level dict."""
    frozen = _freeze_payload(value)
    return dict(frozen.items())


def _builtin_payload_value(value: Any) -> Any:
    if _is_frozen_mapping(value):
        return {
            item_key: _builtin_payload_value(item_value)
            for item_key, item_value in tuple.__iter__(value)
        }
    if type(value) is tuple:
        return [_builtin_payload_value(item) for item in tuple.__iter__(value)]
    return value


def payload_to_builtin(payload: Any) -> dict[str, Any]:
    """Convert a trusted frozen payload after one bounded validation pass."""
    if not _is_frozen_mapping(payload):
        raise ValueError("event payload is not a frozen trusted mapping")
    _frozen_grammar_bytes(payload, depth=0, seen=set())
    return _builtin_payload_value(payload)


class EventContext(_StrictModel):
    """Bounded immutable event context."""

    operation_id: str = Field(default="", max_length=128)
    job_id: str = Field(default="", max_length=128)
    phase: str = Field(default="", max_length=64)
    instance_id: str | None = Field(default=None, max_length=128)
    image_digest: str | None = Field(default=None, max_length=128)

    @field_validator("operation_id", "job_id", "phase", "instance_id", "image_digest")
    @classmethod
    def _identifier(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        if info.field_name is None:
            raise ValueError("identifier validator has no field name")
        return _validate_identifier(value, _IDENTIFIER_LIMITS[info.field_name])


class Event(_StrictModel):
    """One immutable bounded structured event."""

    schema_version: int = Field(default=1, ge=1, le=MAX_SCHEMA_VERSION)
    kind: EventKind
    severity: Severity
    message: str = Field(max_length=MAX_STRING_LENGTH)
    timestamp: datetime = Field(default_factory=utc_now)
    context: EventContext = Field(default_factory=EventContext)
    operation_id: str = Field(default="", max_length=128)
    job_id: str = Field(default="", max_length=128)
    phase: str = Field(default="", max_length=64)
    progress_0_1: float | None = Field(default=None, ge=0, le=1)
    duration_ms: int | None = Field(default=None, ge=0, le=MAX_DURATION_MS)
    recovery_code: str | None = Field(default=None, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("operation_id", "job_id", "phase", "recovery_code")
    @classmethod
    def _identifier(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        if info.field_name is None:
            raise ValueError("identifier validator has no field name")
        return _validate_identifier(value, _IDENTIFIER_LIMITS[info.field_name])

    @field_validator("timestamp")
    @classmethod
    def _utc_timestamp(cls, value: datetime) -> datetime:
        if type(value) is not datetime:
            raise ValueError("timestamp must be an exact datetime")
        try:
            tzinfo = value.tzinfo
        except AttributeError:
            raise ValueError("timestamp timezone is missing") from None
        if tzinfo is not UTC:
            raise ValueError("timestamp must use the exact UTC timezone singleton")
        return value

    @field_validator("payload", mode="before")
    @classmethod
    def _payload(cls, value: Any) -> dict[str, Any]:
        return validate_payload(value)

    @field_validator("progress_0_1", mode="before")
    @classmethod
    def _progress_float(cls, value: Any) -> Any:
        if value is None:
            return None
        if type(value) is not float or not math.isfinite(value):
            raise ValueError("progress_0_1 must be a finite exact float")
        return value

    def model_post_init(self, __context: Any) -> None:
        object.__setattr__(self, "payload", _freeze_payload(self.payload))
