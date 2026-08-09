"""Typed knowledge pipeline models with bounded, frozen contracts.

Top-level collection fields are tuples so durable results/manifests cannot be
growable.  Metadata is validated iteratively on the raw input graph with
bounded depth, item, key, string, and aggregate byte budgets, then copied into
deeply immutable ``FrozenMetadata``/``FrozenMetadataList`` values.  Numeric,
boolean, sequence, and datetime fields use strict before-validators that
reject coercion and hostile containers before Pydantic materializes them.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Iterator
from enum import Enum
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    StringConstraints,
    model_validator,
)

from zana_core.knowledge.limits import (
    HARD_MAX_ACTIONS,
    HARD_MAX_CHUNK_COUNT,
    HARD_MAX_CHUNK_TEXT_BYTES,
    HARD_MAX_CONTEXT_RETAINED_BYTES,
    HARD_MAX_DOCUMENT_RETAINED_BYTES,
    HARD_MAX_EVIDENCE_COUNT,
    HARD_MAX_EVIDENCE_TOKENS,
    HARD_MAX_HEADING_DEPTH,
    HARD_MAX_INT64,
    HARD_MAX_KEY_BYTES,
    HARD_MAX_LINES,
    HARD_MAX_METADATA_DEPTH,
    HARD_MAX_METADATA_ITEMS,
    HARD_MAX_METADATA_RETAINED_BYTES,
    HARD_MAX_PAGE_NUMBER,
    HARD_MAX_PATH_BYTES,
    HARD_MAX_QUERY_BYTES,
    HARD_MAX_SECTION_COUNT,
    HARD_MAX_SNAPSHOT_RETAINED_BYTES,
    HARD_MAX_SOURCE_BYTES,
    HARD_MAX_SOURCE_COUNT,
    HARD_MAX_STRING_BYTES,
    HARD_MAX_TEXT_BYTES,
    HARD_MAX_TOKEN_ESTIMATE,
    HARD_MAX_VECTOR_DIMENSIONS,
    HARD_MAX_WARNINGS,
    MIN_INT64,
    KnowledgeLimits,
    RetainedByteBudget,
    StrictBool,
    StrictFiniteFloatItem,
    StrictFiniteNumber,
    StrictInt,
    StrictUtcDatetime,
    resolve_limits,
    utf8_byte_length,
)


def _byte_guard(max_bytes: int) -> Callable[[str], str]:
    def guard(value: str) -> str:
        utf8_byte_length(value, max_bytes=max_bytes, label="String")
        return value

    return guard


def _no_control_chars(value: str) -> str:
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise ValueError("String must not contain control characters.")
    return value


Utf8Key = Annotated[
    str,
    StringConstraints(max_length=HARD_MAX_KEY_BYTES),
    AfterValidator(_byte_guard(HARD_MAX_KEY_BYTES)),
]
Utf8Path = Annotated[
    str,
    StringConstraints(max_length=HARD_MAX_PATH_BYTES),
    AfterValidator(_byte_guard(HARD_MAX_PATH_BYTES)),
]
Utf8String = Annotated[
    str,
    StringConstraints(max_length=HARD_MAX_STRING_BYTES),
    AfterValidator(_byte_guard(HARD_MAX_STRING_BYTES)),
]
Utf8Text = Annotated[
    str,
    StringConstraints(max_length=HARD_MAX_TEXT_BYTES),
    AfterValidator(_byte_guard(HARD_MAX_TEXT_BYTES)),
]
Utf8ChunkText = Annotated[
    str,
    StringConstraints(max_length=HARD_MAX_CHUNK_TEXT_BYTES),
    AfterValidator(_byte_guard(HARD_MAX_CHUNK_TEXT_BYTES)),
]
Utf8Query = Annotated[
    str,
    StringConstraints(min_length=1, max_length=HARD_MAX_QUERY_BYTES),
    AfterValidator(_byte_guard(HARD_MAX_QUERY_BYTES)),
]
Utf8Identifier = Annotated[
    str,
    StringConstraints(max_length=HARD_MAX_STRING_BYTES),
    AfterValidator(_byte_guard(HARD_MAX_STRING_BYTES)),
    AfterValidator(_no_control_chars),
]

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _canonical_sha256(value: str) -> str:
    if type(value) is not str or not _SHA256_RE.fullmatch(value):
        raise ValueError("Expected a canonical sha256:<64 lowercase hex> digest.")
    return value


def validate_canonical_sha256(value: object) -> str:
    if type(value) is not str or not _SHA256_RE.fullmatch(value):
        raise ValueError("Expected a canonical sha256:<64 lowercase hex> digest.")
    return value


CanonicalSha256 = Annotated[
    str,
    StringConstraints(max_length=71),
    AfterValidator(_canonical_sha256),
]


def _sequence_before(
    value: object,
    *,
    max_length: int,
    label: str,
) -> tuple[object, ...]:
    """Reject hostile iterables before Pydantic materializes a tuple/list."""
    if type(value) is tuple:
        sequence: tuple[object, ...] = value
    elif type(value) is list:
        sequence = tuple(value)
    else:
        raise ValueError(f"{label} must be an exact builtin tuple or list.")
    if len(sequence) > max_length:
        raise ValueError(f"{label} exceeds the {max_length}-item limit.")
    return sequence


def bounded_tuple(max_length: int, label: str) -> BeforeValidator:
    return BeforeValidator(
        lambda value: _sequence_before(
            value,
            max_length=max_length,
            label=label,
        )
    )


def _metadata_to_plain(value: Any) -> Any:
    """Convert immutable metadata to a plain serializable graph."""
    if type(value) is FrozenMetadata:
        validated = FrozenMetadata._validated_wrapper(value)
        return {
            key: _metadata_to_plain(child)
            for key, child in tuple.__getitem__(validated, slice(None))
        }
    if type(value) is FrozenMetadataList:
        validated = FrozenMetadataList._validated_wrapper(value)
        return [_metadata_to_plain(child) for child in tuple.__getitem__(validated, slice(None))]
    return value


_HARD_METADATA_LIMITS = KnowledgeLimits(
    max_metadata_items=HARD_MAX_METADATA_ITEMS,
    max_metadata_depth=HARD_MAX_METADATA_DEPTH,
    max_key_bytes=HARD_MAX_KEY_BYTES,
    max_string_bytes=HARD_MAX_STRING_BYTES,
    max_metadata_retained_bytes=HARD_MAX_METADATA_RETAINED_BYTES,
)


def _metadata_plain(value: Any, *, limits: KnowledgeLimits) -> dict[str, Any]:
    """Validate a raw graph iteratively and return a plain builtin copy.

    Cycles and repeated container aliases are rejected before any recursive
    copy is constructed.  Only exact builtin dict/list containers and exact
    primitive scalars are accepted; subclasses and hostile containers are
    rejected without invoking ``items``/``iter``/``len`` on them.  Integers
    are limited to signed 64-bit magnitude and an aggregate metadata UTF-8
    retained budget is enforced.
    """
    if type(value) is not dict:
        raise ValueError("Metadata must be an exact builtin mapping.")
    plain: dict[str, Any] = {}
    seen: set[int] = set()
    item_count = 0
    retained_bytes = 0
    stack: list[tuple[Any, Any, int]] = [(value, plain, 1)]
    while stack:
        node, target, depth = stack.pop()
        if depth > limits.max_metadata_depth:
            raise ValueError(f"Metadata exceeds the {limits.max_metadata_depth}-level depth limit.")
        node_id = id(node)
        if node_id in seen:
            raise ValueError("Metadata contains a cycle or repeated container alias.")
        seen.add(node_id)
        if type(node) is dict:
            if len(node) > limits.max_metadata_items:
                raise ValueError(f"Metadata exceeds the {limits.max_metadata_items}-item limit.")
            for key, child in node.items():
                item_count += 1
                if item_count > limits.max_metadata_items:
                    raise ValueError(
                        f"Metadata exceeds the {limits.max_metadata_items}-item limit."
                    )
                if type(key) is not str:
                    raise ValueError("Metadata keys must be exact strings.")
                retained_bytes += utf8_byte_length(
                    key,
                    max_bytes=limits.max_key_bytes,
                    label="Metadata key",
                )
                if retained_bytes > limits.max_metadata_retained_bytes:
                    raise ValueError("Metadata exceeds the aggregate UTF-8 retained byte limit.")
                if type(child) is dict:
                    dict_copy: dict[str, Any] = {}
                    target[key] = dict_copy
                    stack.append((child, dict_copy, depth + 1))
                elif type(child) is list:
                    list_copy: list[Any] = []
                    target[key] = list_copy
                    stack.append((child, list_copy, depth + 1))
                else:
                    scalar = _metadata_scalar(child, limits=limits)
                    retained_bytes += _scalar_string_bytes(scalar, limits=limits)
                    if retained_bytes > limits.max_metadata_retained_bytes:
                        raise ValueError(
                            "Metadata exceeds the aggregate UTF-8 retained byte limit."
                        )
                    target[key] = scalar
            continue
        if type(node) is list:
            if len(node) > limits.max_metadata_items:
                raise ValueError(f"Metadata exceeds the {limits.max_metadata_items}-item limit.")
            for child in node:
                item_count += 1
                if item_count > limits.max_metadata_items:
                    raise ValueError(
                        f"Metadata exceeds the {limits.max_metadata_items}-item limit."
                    )
                if type(child) is dict:
                    dict_copy = {}
                    target.append(dict_copy)
                    stack.append((child, dict_copy, depth + 1))
                elif type(child) is list:
                    list_copy = []
                    target.append(list_copy)
                    stack.append((child, list_copy, depth + 1))
                else:
                    scalar = _metadata_scalar(child, limits=limits)
                    retained_bytes += _scalar_string_bytes(scalar, limits=limits)
                    if retained_bytes > limits.max_metadata_retained_bytes:
                        raise ValueError(
                            "Metadata exceeds the aggregate UTF-8 retained byte limit."
                        )
                    target.append(scalar)
            continue
        raise ValueError("Metadata values must be exact builtin mappings or lists.")
    return plain


def _metadata_scalar(value: Any, *, limits: KnowledgeLimits) -> Any:
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if not (MIN_INT64 <= value <= HARD_MAX_INT64):
            raise ValueError("Metadata integers must fit in signed 64-bit range.")
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("Metadata floats must be finite.")
        return value
    if type(value) is str:
        utf8_byte_length(value, max_bytes=limits.max_string_bytes, label="Metadata string")
        return value
    raise ValueError("Metadata values must be JSON-compatible exact primitives.")


def _is_metadata_builtin(value: Any) -> bool:
    return (
        type(value) is FrozenMetadata
        or type(value) is FrozenMetadataList
        or type(value) in (type(None), bool, int, float, str)
    )


def _scalar_string_bytes(value: Any, *, limits: KnowledgeLimits) -> int:
    if type(value) is str:
        return utf8_byte_length(
            value,
            max_bytes=limits.max_string_bytes,
            label="Metadata string",
        )
    return 0


def _freeze_plain_metadata(value: Any) -> Any:
    if type(value) is dict:
        return FrozenMetadata._build_from_plain(value)
    if type(value) is list:
        return FrozenMetadataList._build_from_plain(tuple(value))
    return value


class _FrozenGraphContext:
    __slots__ = ("limits", "seen", "item_count", "retained_bytes")

    def __init__(self, limits: KnowledgeLimits) -> None:
        self.limits = limits
        self.seen: set[int] = set()
        self.item_count = 0
        self.retained_bytes = 0


def _validate_frozen_entries(
    value: FrozenMetadata | FrozenMetadataList,
    *,
    context: _FrozenGraphContext,
    depth: int,
    is_mapping: bool,
) -> tuple[Any, ...]:
    """Validate and freeze one raw exact tuple-subclass graph.

    Only ``tuple.__len__``/``tuple.__iter__``/``tuple.__getitem__`` are used on
    raw wrappers; one shared seen set and all budgets propagate through the
    whole graph.  Overridden methods, equality, and repr are never invoked.
    """
    limits = context.limits
    if depth > limits.max_metadata_depth:
        raise ValueError(f"Metadata exceeds the {limits.max_metadata_depth}-level depth limit.")
    if tuple.__len__(value) > limits.max_metadata_items:
        raise ValueError(f"Metadata exceeds the {limits.max_metadata_items}-item limit.")
    node_id = id(value)
    if node_id in context.seen:
        raise ValueError("Metadata contains a cycle or repeated container alias.")
    context.seen.add(node_id)
    frozen: list[Any] = []
    seen_keys: set[str] = set()
    for raw_item in tuple.__iter__(value):
        context.item_count += 1
        if context.item_count > limits.max_metadata_items:
            raise ValueError(f"Metadata exceeds the {limits.max_metadata_items}-item limit.")
        if is_mapping:
            if type(raw_item) is not tuple or tuple.__len__(raw_item) != 2:
                raise ValueError("Frozen metadata mapping entries must be exact two-tuples.")
            key = tuple.__getitem__(raw_item, 0)
            child = tuple.__getitem__(raw_item, 1)
            if type(key) is not str or key in seen_keys:
                raise ValueError("Frozen metadata keys must be exact unique strings.")
            seen_keys.add(key)
            context.retained_bytes += utf8_byte_length(
                key,
                max_bytes=limits.max_key_bytes,
                label="Metadata key",
            )
            if context.retained_bytes > limits.max_metadata_retained_bytes:
                raise ValueError("Metadata exceeds the aggregate UTF-8 retained byte limit.")
            frozen.append((key, _freeze_frozen_child(child, context=context, depth=depth + 1)))
            continue
        frozen.append(_freeze_frozen_child(raw_item, context=context, depth=depth + 1))
    return tuple(frozen)


def _freeze_frozen_child(
    child: Any,
    *,
    context: _FrozenGraphContext,
    depth: int,
) -> Any:
    if type(child) is FrozenMetadata:
        frozen = _validate_frozen_entries(
            child,
            context=context,
            depth=depth,
            is_mapping=True,
        )
        return tuple.__new__(FrozenMetadata, frozen)
    if type(child) is FrozenMetadataList:
        frozen = _validate_frozen_entries(
            child,
            context=context,
            depth=depth,
            is_mapping=False,
        )
        return tuple.__new__(FrozenMetadataList, frozen)
    if type(child) in (type(None), bool, int, float, str):
        if type(child) is str:
            context.retained_bytes += utf8_byte_length(
                child,
                max_bytes=context.limits.max_string_bytes,
                label="Metadata string",
            )
        elif type(child) is float and not math.isfinite(child):
            raise ValueError("Metadata floats must be finite.")
        elif type(child) is int and not (MIN_INT64 <= child <= HARD_MAX_INT64):
            raise ValueError("Metadata integers must fit in signed 64-bit range.")
        if context.retained_bytes > context.limits.max_metadata_retained_bytes:
            raise ValueError("Metadata exceeds the aggregate UTF-8 retained byte limit.")
        return child
    raise ValueError("Frozen metadata contains an invalid value.")


class FrozenMetadata(tuple):
    """Deeply immutable metadata mapping.

    This is intentionally not a ``dict`` subclass: inherited dict mutators,
    ``|=``, and base-class calls cannot mutate the stored graph.  Nested
    containers are returned as frozen views and the backing storage is an
    immutable tuple of key/value pairs, so mutation through returned views,
    private attribute access, or ``object.__setattr__`` is impossible.
    """

    __slots__ = ()

    def __new__(cls, mapping: dict[str, Any] | None = None) -> FrozenMetadata:
        if mapping is not None:
            plain = _metadata_plain(mapping, limits=_HARD_METADATA_LIMITS)
            frozen = tuple.__new__(
                cls,
                tuple((key, _freeze_plain_metadata(child)) for key, child in plain.items()),
            )
            return cls._build_from_plain_entries(frozen)
        return tuple.__new__(cls, ())

    @classmethod
    def _build_from_plain(cls, plain: dict[str, Any]) -> FrozenMetadata:
        """Build from an exact validated plain graph only."""
        if type(plain) is not dict:
            raise ValueError("FrozenMetadata requires an exact validated plain mapping.")
        plain = _metadata_plain(plain, limits=_HARD_METADATA_LIMITS)
        frozen = tuple.__new__(
            cls,
            tuple((key, _freeze_plain_metadata(child)) for key, child in plain.items()),
        )
        return cls._build_from_plain_entries(frozen)

    @classmethod
    def _build_from_plain_entries(cls, entries: tuple[tuple[str, Any], ...]) -> FrozenMetadata:
        if type(entries) is not FrozenMetadata:
            raise ValueError("FrozenMetadata backing must be an exact tuple.")
        frozen = _validate_frozen_entries(
            entries,
            context=_FrozenGraphContext(_HARD_METADATA_LIMITS),
            depth=1,
            is_mapping=True,
        )
        return tuple.__new__(cls, frozen)

    @classmethod
    def _from_plain(cls, plain: dict[str, Any]) -> FrozenMetadata:
        return cls._build_from_plain(plain)

    @classmethod
    def _from_plain_entries(cls, entries: tuple[tuple[str, Any], ...]) -> FrozenMetadata:
        if type(entries) is tuple:
            entries = tuple.__new__(cls, entries)
        elif type(entries) is not FrozenMetadata:
            raise ValueError(
                "FrozenMetadata._from_plain_entries requires an exact tuple or wrapper."
            )
        return cls._build_from_plain_entries(entries)

    def __setattr__(self, name: str, value: Any) -> None:
        raise TypeError("FrozenMetadata does not support attribute assignment.")

    def __delattr__(self, name: str) -> None:
        raise TypeError("FrozenMetadata does not support attribute deletion.")

    @classmethod
    def _validated_wrapper(cls, wrapper: object) -> FrozenMetadata:
        """Revalidate a structurally immutable wrapper before traversal."""
        if type(wrapper) is not cls:
            raise ValueError("Metadata wrapper must be an exact FrozenMetadata instance.")
        frozen = _validate_frozen_entries(
            wrapper,
            context=_FrozenGraphContext(_HARD_METADATA_LIMITS),
            depth=1,
            is_mapping=True,
        )
        return tuple.__new__(cls, frozen)

    def __getitem__(self, key: str | int) -> Any:  # type: ignore[override]
        if type(key) is not str:
            raise KeyError(key)
        validated = FrozenMetadata._validated_wrapper(self)
        for entry_key, entry_value in tuple.__iter__(validated):
            if entry_key == key:
                return entry_value
        raise KeyError(key)

    def keys(self) -> tuple[str, ...]:
        validated = FrozenMetadata._validated_wrapper(self)
        return tuple(key for key, _ in tuple.__getitem__(validated, slice(None)))

    def items(self) -> tuple[tuple[str, Any], ...]:
        validated = FrozenMetadata._validated_wrapper(self)
        return tuple.__getitem__(validated, slice(None))

    def values(self) -> tuple[Any, ...]:
        validated = FrozenMetadata._validated_wrapper(self)
        return tuple(child for _, child in tuple.__getitem__(validated, slice(None)))

    def __iter__(self) -> Iterator[str]:
        validated = FrozenMetadata._validated_wrapper(self)
        return (key for key, _ in tuple.__iter__(validated))

    def __len__(self) -> int:
        FrozenMetadata._validated_wrapper(self)
        return tuple.__len__(self)

    def __contains__(self, key: object) -> bool:
        if type(key) is not str:
            return False
        validated = FrozenMetadata._validated_wrapper(self)
        return any(entry_key == key for entry_key, _ in tuple.__iter__(validated))

    def get(self, key: str, default: Any = None) -> Any:
        if type(key) is not str:
            return default
        validated = FrozenMetadata._validated_wrapper(self)
        for entry_key, entry_value in tuple.__iter__(validated):
            if entry_key == key:
                return entry_value
        return default

    def copy(self) -> FrozenMetadata:
        return FrozenMetadata._validated_wrapper(self)

    @property
    def _data(self) -> FrozenMetadata:
        return FrozenMetadata._validated_wrapper(self)

    def __eq__(self, other: object) -> bool:
        if type(other) is FrozenMetadata:
            left = FrozenMetadata._validated_wrapper(self)
            right = FrozenMetadata._validated_wrapper(other)
            return tuple.__getitem__(left, slice(None)) == tuple.__getitem__(right, slice(None))
        if type(other) is dict:
            left = FrozenMetadata._validated_wrapper(self)
            other_plain = _metadata_plain(other, limits=_HARD_METADATA_LIMITS)
            return dict(tuple.__getitem__(left, slice(None))) == other_plain
        return False

    def __repr__(self) -> str:
        validated = FrozenMetadata._validated_wrapper(self)
        return repr(dict(tuple.__getitem__(validated, slice(None))))

    def __ior__(self, other: object) -> FrozenMetadata:
        raise TypeError("FrozenMetadata does not support in-place union.")

    def __or__(self, other: object) -> FrozenMetadata:
        if type(other) is dict:
            other_plain = _metadata_plain(other, limits=_HARD_METADATA_LIMITS)
        elif type(other) is FrozenMetadata:
            other_plain = _metadata_to_plain(FrozenMetadata._validated_wrapper(other))
        else:
            raise TypeError("FrozenMetadata union requires an exact mapping.")
        merged = _metadata_to_plain(FrozenMetadata._validated_wrapper(self))
        merged.update(other_plain)
        return FrozenMetadata(merged)


class FrozenMetadataList(tuple):
    """Deeply immutable metadata sequence."""

    __slots__ = ()

    def __new__(cls, values: tuple[Any, ...] | list[Any] | None = None) -> FrozenMetadataList:
        if values is not None:
            if type(values) not in (tuple, list):
                raise ValueError("FrozenMetadataList requires an exact builtin sequence.")
            plain = _metadata_plain({"items": list(values)}, limits=_HARD_METADATA_LIMITS)
            entries = tuple(_freeze_plain_metadata(v) for v in plain["items"])
            frozen = tuple.__new__(cls, entries)
            return cls._build_from_plain_entries(frozen)
        return tuple.__new__(cls, ())

    @classmethod
    def _build_from_plain(cls, values: tuple[Any, ...]) -> FrozenMetadataList:
        """Build from an exact validated plain tuple only."""
        if type(values) is not tuple:
            raise ValueError("FrozenMetadataList requires an exact validated plain tuple.")
        frozen = tuple.__new__(
            cls,
            tuple(_freeze_plain_metadata(v) for v in values),
        )
        return cls._build_from_plain_entries(frozen)

    @classmethod
    def _build_from_plain_entries(cls, entries: tuple[Any, ...]) -> FrozenMetadataList:
        if type(entries) is not FrozenMetadataList:
            raise ValueError("FrozenMetadataList backing must be an exact tuple.")
        frozen = _validate_frozen_entries(
            entries,
            context=_FrozenGraphContext(_HARD_METADATA_LIMITS),
            depth=1,
            is_mapping=False,
        )
        return tuple.__new__(cls, frozen)

    @classmethod
    def _from_plain(cls, values: tuple[Any, ...]) -> FrozenMetadataList:
        return cls._build_from_plain(values)

    @classmethod
    def _from_plain_entries(cls, entries: tuple[Any, ...]) -> FrozenMetadataList:
        if type(entries) is tuple:
            entries = tuple.__new__(cls, entries)
        elif type(entries) is not FrozenMetadataList:
            raise ValueError(
                "FrozenMetadataList._from_plain_entries requires an exact tuple or wrapper."
            )
        return cls._build_from_plain_entries(entries)

    def __setattr__(self, name: str, value: Any) -> None:
        raise TypeError("FrozenMetadataList does not support attribute assignment.")

    def __delattr__(self, name: str) -> None:
        raise TypeError("FrozenMetadataList does not support attribute deletion.")

    @classmethod
    def _validated_wrapper(cls, wrapper: object) -> FrozenMetadataList:
        """Revalidate a structurally immutable wrapper before traversal."""
        if type(wrapper) is not cls:
            raise ValueError("Metadata wrapper must be an exact FrozenMetadataList instance.")
        frozen = _validate_frozen_entries(
            wrapper,
            context=_FrozenGraphContext(_HARD_METADATA_LIMITS),
            depth=1,
            is_mapping=False,
        )
        return tuple.__new__(cls, frozen)

    def __getitem__(self, index: int | slice) -> Any:  # type: ignore[override]
        if type(index) is not int and type(index) is not slice:
            raise TypeError("FrozenMetadataList indices must be exact int or slice.")
        if type(index) is slice:
            validated = FrozenMetadataList._validated_wrapper(self)
            return tuple.__getitem__(validated, index)
        validated = FrozenMetadataList._validated_wrapper(self)
        return tuple.__getitem__(validated, index)

    def __iter__(self) -> Iterator[Any]:
        validated = FrozenMetadataList._validated_wrapper(self)
        return tuple.__iter__(validated)

    def __len__(self) -> int:
        FrozenMetadataList._validated_wrapper(self)
        return tuple.__len__(self)

    def __contains__(self, value: object) -> bool:
        if not _is_metadata_builtin(value):
            return False
        validated = FrozenMetadataList._validated_wrapper(self)
        for item in tuple.__getitem__(validated, slice(None)):
            if type(item) is type(value) and item == value:
                return True
        return False

    def copy(self) -> FrozenMetadataList:
        return FrozenMetadataList._validated_wrapper(self)

    @property
    def _data(self) -> FrozenMetadataList:
        return FrozenMetadataList._validated_wrapper(self)

    def __eq__(self, other: object) -> bool:
        try:
            if type(other) is tuple or type(other) is list:
                other_values = FrozenMetadataList(other)
            elif type(other) is FrozenMetadataList:
                other_values = FrozenMetadataList._validated_wrapper(other)
            else:
                return False
        except ValueError:
            return False
        left = tuple.__getitem__(FrozenMetadataList._validated_wrapper(self), slice(None))
        right = tuple.__getitem__(
            FrozenMetadataList._validated_wrapper(other_values),
            slice(None),
        )
        return left == right

    def __repr__(self) -> str:
        validated = FrozenMetadataList._validated_wrapper(self)
        return repr(list(tuple.__getitem__(validated, slice(None))))

    def __iadd__(self, other: object) -> FrozenMetadataList:
        raise TypeError("FrozenMetadataList does not support in-place addition.")

    def __imul__(self, other: object) -> FrozenMetadataList:
        raise TypeError("FrozenMetadataList does not support in-place multiplication.")

    def append(self, value: Any) -> None:
        raise TypeError("FrozenMetadataList does not support append.")

    def extend(self, values: Any) -> None:
        raise TypeError("FrozenMetadataList does not support extend.")

    def insert(self, index: int, value: Any) -> None:
        raise TypeError("FrozenMetadataList does not support insert.")

    def pop(self, index: int = -1) -> Any:
        raise TypeError("FrozenMetadataList does not support pop.")

    def remove(self, value: Any) -> None:
        raise TypeError("FrozenMetadataList does not support remove.")

    def clear(self) -> None:
        raise TypeError("FrozenMetadataList does not support clear.")

    def sort(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("FrozenMetadataList does not support sort.")

    def reverse(self) -> None:
        raise TypeError("FrozenMetadataList does not support reverse.")


def validate_bounded_metadata(
    value: object,
    *,
    limits: KnowledgeLimits | None = None,
) -> FrozenMetadata:
    """Validate a raw graph iteratively, then build an immutable copy."""
    active = _HARD_METADATA_LIMITS if limits is None else resolve_limits(limits)
    return FrozenMetadata._build_from_plain(_metadata_plain(value, limits=active))


def _metadata_before(value: object) -> FrozenMetadata:
    return validate_bounded_metadata(value, limits=_HARD_METADATA_LIMITS)


BoundedMetadata = Annotated[
    Any,
    BeforeValidator(_metadata_before),
    PlainSerializer(_metadata_to_plain, return_type=dict, when_used="always"),
]


def _vector_before(value: object) -> tuple[object, ...]:
    return _sequence_before(
        value,
        max_length=HARD_MAX_VECTOR_DIMENSIONS,
        label="Vector",
    )


BoundedVector = Annotated[
    tuple[StrictFiniteFloatItem, ...],
    BeforeValidator(_vector_before),
    Field(max_length=HARD_MAX_VECTOR_DIMENSIONS),
]


class DocumentKind(str, Enum):
    """Supported document source kinds."""

    MARKDOWN = "markdown"
    TEXT = "text"
    PDF = "pdf"
    UNSUPPORTED = "unsupported"


class ParserWarning(BaseModel):
    """Structured non-fatal warning from normalization."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: Utf8String
    message: Utf8String
    line: StrictInt | None = Field(default=None, ge=1, le=HARD_MAX_LINES)
    column: StrictInt | None = Field(default=None, ge=1, le=HARD_MAX_LINES)


class ParserError(BaseModel):
    """Structured fatal parser error that prevents use of a document."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: Utf8String
    message: Utf8String
    recoverable: StrictBool = False
    actions: ParserActions = Field(default_factory=tuple, max_length=HARD_MAX_ACTIONS)


class SourceMetadata(BaseModel):
    """Metadata recorded from the approved intake step."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    original_path: Utf8Path
    display_name: Utf8String
    kind: DocumentKind
    size_bytes: StrictInt = Field(ge=0, le=HARD_MAX_SOURCE_BYTES)
    sha256: CanonicalSha256
    approved: StrictBool = True
    extra: BoundedMetadata = Field(default_factory=FrozenMetadata)


class NormalizedSection(BaseModel):
    """One normalized section with its heading path and offsets."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    section_id: Utf8String
    heading_path: HeadingPath = Field(default_factory=tuple, max_length=HARD_MAX_HEADING_DEPTH)
    page_start: StrictInt | None = Field(default=None, ge=1, le=HARD_MAX_PAGE_NUMBER)
    page_end: StrictInt | None = Field(default=None, ge=1, le=HARD_MAX_PAGE_NUMBER)
    text: Utf8Text = ""
    start_offset: StrictInt = Field(ge=0, le=HARD_MAX_TEXT_BYTES)
    end_offset: StrictInt = Field(ge=0, le=HARD_MAX_TEXT_BYTES)

    @model_validator(mode="after")
    def _offsets_ordered(self) -> NormalizedSection:
        if self.end_offset < self.start_offset:
            raise ValueError("end_offset must be >= start_offset.")
        return self


class NormalizedDocument(BaseModel):
    """Canonical normalized document."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: Utf8String
    title: Utf8String
    sections: DocumentSections = Field(default_factory=tuple, max_length=HARD_MAX_SECTION_COUNT)
    warnings: DocumentWarnings = Field(default_factory=tuple, max_length=HARD_MAX_WARNINGS)

    @model_validator(mode="after")
    def _bounded_retained_bytes(self) -> NormalizedDocument:
        budget = RetainedByteBudget(HARD_MAX_DOCUMENT_RETAINED_BYTES, label="Normalized document")
        budget.add(self.document_id, max_bytes=HARD_MAX_STRING_BYTES, label="document_id")
        budget.add(self.title, max_bytes=HARD_MAX_STRING_BYTES, label="title")
        for section in self.sections:
            budget.add(section.section_id, max_bytes=HARD_MAX_STRING_BYTES, label="section_id")
            budget.add(section.text, max_bytes=HARD_MAX_TEXT_BYTES, label="section text")
            for heading in section.heading_path:
                budget.add(heading, max_bytes=HARD_MAX_STRING_BYTES, label="heading")
        for warning in self.warnings:
            budget.add(warning.code, max_bytes=HARD_MAX_STRING_BYTES, label="warning code")
            budget.add(warning.message, max_bytes=HARD_MAX_STRING_BYTES, label="warning message")
        return self


class Chunk(BaseModel):
    """Deterministic structural chunk."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chunk_id: Utf8String
    document_digest: CanonicalSha256
    section_id: Utf8String
    heading_path: HeadingPath = Field(default_factory=tuple, max_length=HARD_MAX_HEADING_DEPTH)
    page_start: StrictInt | None = Field(default=None, ge=1, le=HARD_MAX_PAGE_NUMBER)
    page_end: StrictInt | None = Field(default=None, ge=1, le=HARD_MAX_PAGE_NUMBER)
    start_offset: StrictInt = Field(ge=0, le=HARD_MAX_TEXT_BYTES)
    end_offset: StrictInt = Field(ge=0, le=HARD_MAX_TEXT_BYTES)
    text: Utf8ChunkText = ""
    token_estimate: StrictInt = Field(ge=0, le=HARD_MAX_TOKEN_ESTIMATE)
    tokenizer_identity: Utf8String
    overlap_prefix: Utf8ChunkText | None = None
    metadata_json: BoundedMetadata = Field(default_factory=FrozenMetadata)

    @model_validator(mode="after")
    def _offsets_ordered(self) -> Chunk:
        if self.end_offset < self.start_offset:
            raise ValueError("end_offset must be >= start_offset.")
        return self


class ChunkConfiguration(BaseModel):
    """Configuration used by zana.heading-aware.v1."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_tokens: StrictInt = Field(default=640, ge=1, le=HARD_MAX_TOKEN_ESTIMATE)
    max_tokens: StrictInt = Field(default=900, ge=1, le=HARD_MAX_TOKEN_ESTIMATE)
    overlap_tokens: StrictInt = Field(default=64, ge=0, le=HARD_MAX_TOKEN_ESTIMATE)
    tokenizer_identity: Utf8String = "zana.text-estimator.v1"
    min_chunk_tokens: StrictInt = Field(default=1, ge=1, le=HARD_MAX_TOKEN_ESTIMATE)

    @model_validator(mode="after")
    def _validate_token_relationships(self) -> ChunkConfiguration:
        if self.max_tokens < self.target_tokens:
            raise ValueError("max_tokens must be >= target_tokens.")
        if self.overlap_tokens >= self.max_tokens:
            raise ValueError("overlap_tokens must be < max_tokens.")
        if self.min_chunk_tokens > self.target_tokens:
            raise ValueError("min_chunk_tokens must be <= target_tokens.")
        return self


class SnapshotManifest(BaseModel):
    """Immutable knowledge snapshot manifest with invalidation inputs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_id: CanonicalSha256
    parser_version: Utf8String
    chunk_config: ChunkConfiguration
    embedding_identity_required: Utf8String
    sources: SnapshotSources = Field(default_factory=tuple, max_length=HARD_MAX_SOURCE_COUNT)
    chunks: SnapshotChunks = Field(default_factory=tuple, max_length=HARD_MAX_CHUNK_COUNT)
    created_at: StrictUtcDatetime

    @model_validator(mode="after")
    def _bounded_retained_bytes(self) -> SnapshotManifest:
        budget = RetainedByteBudget(HARD_MAX_SNAPSHOT_RETAINED_BYTES, label="Snapshot manifest")
        budget.add(self.snapshot_id, max_bytes=HARD_MAX_STRING_BYTES, label="snapshot_id")
        budget.add(self.parser_version, max_bytes=HARD_MAX_STRING_BYTES, label="parser_version")
        budget.add(
            self.embedding_identity_required,
            max_bytes=HARD_MAX_STRING_BYTES,
            label="embedding identity",
        )
        budget.add(
            self.chunk_config.tokenizer_identity,
            max_bytes=HARD_MAX_STRING_BYTES,
            label="tokenizer identity",
        )
        for source in self.sources:
            budget.add(source.original_path, max_bytes=HARD_MAX_PATH_BYTES, label="source path")
            budget.add(source.display_name, max_bytes=HARD_MAX_STRING_BYTES, label="source name")
            budget.add(source.sha256, max_bytes=HARD_MAX_STRING_BYTES, label="source hash")
            _account_metadata(budget, source.extra)
        for chunk in self.chunks:
            budget.add(chunk.chunk_id, max_bytes=HARD_MAX_STRING_BYTES, label="chunk_id")
            budget.add(chunk.document_digest, max_bytes=HARD_MAX_STRING_BYTES, label="digest")
            budget.add(chunk.section_id, max_bytes=HARD_MAX_STRING_BYTES, label="section_id")
            budget.add(chunk.text, max_bytes=HARD_MAX_CHUNK_TEXT_BYTES, label="chunk text")
            budget.add(
                chunk.tokenizer_identity,
                max_bytes=HARD_MAX_STRING_BYTES,
                label="tokenizer identity",
            )
            if chunk.overlap_prefix is not None:
                budget.add(
                    chunk.overlap_prefix,
                    max_bytes=HARD_MAX_CHUNK_TEXT_BYTES,
                    label="overlap prefix",
                )
            for heading in chunk.heading_path:
                budget.add(heading, max_bytes=HARD_MAX_STRING_BYTES, label="heading")
            _account_metadata(budget, chunk.metadata_json)
        return self


class EvidenceBlock(BaseModel):
    """Structured evidence block used for citations and context."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: CanonicalSha256
    source_title: Utf8String
    page: StrictInt | None = Field(default=None, ge=1, le=HARD_MAX_PAGE_NUMBER)
    section: Utf8String | None = None
    heading_path: HeadingPath = Field(default_factory=tuple, max_length=HARD_MAX_HEADING_DEPTH)
    text: Utf8ChunkText = ""
    token_estimate: StrictInt = Field(ge=0, le=HARD_MAX_TOKEN_ESTIMATE)
    similarity: StrictFiniteNumber | None = Field(default=None, ge=-1, le=1)


class ContextPackage(BaseModel):
    """Context fitted to deterministic token and byte budgets."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence: ContextEvidence = Field(default_factory=tuple, max_length=HARD_MAX_EVIDENCE_COUNT)
    total_tokens: StrictInt = Field(default=0, ge=0, le=HARD_MAX_EVIDENCE_TOKENS)
    total_bytes: StrictInt = Field(default=0, ge=0, le=HARD_MAX_CONTEXT_RETAINED_BYTES)
    fitted: StrictBool = True

    @model_validator(mode="after")
    def _consistent_and_bounded(self) -> ContextPackage:
        from zana_core.knowledge.chunker import TextEstimator
        from zana_core.knowledge.evidence import render_evidence_block

        budget = RetainedByteBudget(HARD_MAX_CONTEXT_RETAINED_BYTES, label="Context package")
        estimator = TextEstimator()
        expected_tokens = 0
        expected_bytes = 0
        for block in self.evidence:
            budget.add(block.source_id, max_bytes=HARD_MAX_STRING_BYTES, label="source_id")
            budget.add(
                block.source_title,
                max_bytes=HARD_MAX_STRING_BYTES,
                label="source_title",
            )
            if block.section is not None:
                budget.add(block.section, max_bytes=HARD_MAX_STRING_BYTES, label="section")
            for heading in block.heading_path:
                budget.add(heading, max_bytes=HARD_MAX_STRING_BYTES, label="heading")
            budget.add(block.text, max_bytes=HARD_MAX_CHUNK_TEXT_BYTES, label="evidence text")
            rendered = render_evidence_block(block)
            expected_tokens += estimator.estimate(rendered)
            expected_bytes += utf8_byte_length(
                rendered,
                max_bytes=HARD_MAX_CONTEXT_RETAINED_BYTES,
                label="Rendered evidence",
            )
        if self.total_tokens != expected_tokens or self.total_bytes != expected_bytes:
            raise ValueError("ContextPackage totals must match the accepted rendered context.")
        return self


HeadingPath = Annotated[
    tuple[Utf8String, ...],
    bounded_tuple(HARD_MAX_HEADING_DEPTH, "Heading path"),
]
ParserActions = Annotated[
    tuple[Utf8String, ...],
    bounded_tuple(HARD_MAX_ACTIONS, "Parser actions"),
]
DocumentSections = Annotated[
    tuple[NormalizedSection, ...],
    bounded_tuple(HARD_MAX_SECTION_COUNT, "Document sections"),
]
DocumentWarnings = Annotated[
    tuple[ParserWarning, ...],
    bounded_tuple(HARD_MAX_WARNINGS, "Document warnings"),
]
SnapshotSources = Annotated[
    tuple[SourceMetadata, ...],
    bounded_tuple(HARD_MAX_SOURCE_COUNT, "Snapshot sources"),
]
SnapshotChunks = Annotated[
    tuple[Chunk, ...],
    bounded_tuple(HARD_MAX_CHUNK_COUNT, "Snapshot chunks"),
]
ContextEvidence = Annotated[
    tuple[EvidenceBlock, ...],
    bounded_tuple(HARD_MAX_EVIDENCE_COUNT, "Context evidence"),
]


def _account_metadata(budget: RetainedByteBudget, metadata: Any) -> None:
    stack: list[Any] = [metadata]
    while stack:
        node = stack.pop()
        if type(node) is FrozenMetadata:
            validated = FrozenMetadata._validated_wrapper(node)
            for key, child in tuple.__getitem__(validated, slice(None)):
                budget.add(key, max_bytes=HARD_MAX_KEY_BYTES, label="metadata key")
                if type(child) in (FrozenMetadata, FrozenMetadataList):
                    stack.append(child)
                elif type(child) is str:
                    budget.add(child, max_bytes=HARD_MAX_STRING_BYTES, label="metadata string")
        elif type(node) is FrozenMetadataList:
            validated = FrozenMetadataList._validated_wrapper(node)
            for child in tuple.__getitem__(validated, slice(None)):
                if type(child) in (FrozenMetadata, FrozenMetadataList):
                    stack.append(child)
                elif type(child) is str:
                    budget.add(child, max_bytes=HARD_MAX_STRING_BYTES, label="metadata string")


def canonical_identity_key(*, parts: dict[str, object]) -> str:
    """Return a delimiter-independent canonical identity digest.

    Accepts only an exact builtin mapping of scalar values; hostile mappings
    and non-scalar values are rejected before any JSON allocation.
    """
    if type(parts) is not dict:
        raise ValueError("Identity parts must be an exact builtin mapping.")
    if len(parts) > 64:
        raise ValueError("Identity parts exceed the 64-entry limit.")
    entries: list[tuple[str, object]] = []
    for key in sorted(parts):
        if type(key) is not str:
            raise ValueError("Identity keys must be exact strings.")
        value = parts[key]
        if value is None or type(value) in (bool, int, float, str):
            if type(value) is float and not math.isfinite(value):
                raise ValueError("Identity floats must be finite.")
            if type(value) is int and not (MIN_INT64 <= value <= HARD_MAX_INT64):
                raise ValueError("Identity integers must fit in signed 64-bit range.")
            entries.append((key, value))
            continue
        raise ValueError("Identity values must be scalar primitives.")
    payload = json.dumps(
        entries,
        sort_keys=False,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"
