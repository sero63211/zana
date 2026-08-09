"""Frozen hard-capped resource limits and bounded accounting for knowledge.

Every knowledge stage validates against :class:`KnowledgeLimits` before
allocating work.  Defaults are conservative for 16 GB/low-disk laptops; each
field carries a Pydantic ``le`` hard cap so callers cannot raise a limit past
its cap.  Values that are streamed (source bytes) may use larger hard caps;
values retained in object graphs are deliberately much smaller.
"""

from __future__ import annotations

import math
import time
from datetime import UTC, datetime
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

HARD_MAX_SOURCE_BYTES = 64 * 1024 * 1024
HARD_MAX_SOURCE_COUNT = 256
HARD_MAX_SECTION_COUNT = 4_096
HARD_MAX_CHUNK_COUNT = 8_192
HARD_MAX_TEXT_BYTES = 4 * 1024 * 1024
HARD_MAX_CHUNK_TEXT_BYTES = 1024 * 1024
HARD_MAX_DOCUMENT_RETAINED_BYTES = 16 * 1024 * 1024
HARD_MAX_SNAPSHOT_RETAINED_BYTES = 24 * 1024 * 1024
HARD_MAX_INDEX_RETAINED_BYTES = 24 * 1024 * 1024
HARD_MAX_RESULT_RETAINED_BYTES = 8 * 1024 * 1024
HARD_MAX_CONTEXT_RETAINED_BYTES = 8 * 1024 * 1024
HARD_MAX_CONTEXT_BYTES = HARD_MAX_CONTEXT_RETAINED_BYTES
HARD_MAX_METADATA_RETAINED_BYTES = 1024 * 1024
HARD_MAX_QUERY_BYTES = 64 * 1024
HARD_MAX_PATH_BYTES = 8_192
HARD_MAX_ENDPOINT_BYTES = 8_192
HARD_MAX_KEY_BYTES = 1_024
HARD_MAX_STRING_BYTES = 16 * 1024
HARD_MAX_CREDENTIAL_BYTES = 2_048
HARD_MAX_LINES = 200_000
HARD_MAX_HEADING_DEPTH = 16
HARD_MAX_METADATA_ITEMS = 256
HARD_MAX_METADATA_DEPTH = 8
HARD_MAX_BATCH_TEXT_COUNT = 128
HARD_MAX_BATCH_TOTAL_BYTES = 4 * 1024 * 1024
HARD_MAX_REQUEST_BYTES = 8 * 1024 * 1024
HARD_MAX_TIMEOUT_SECONDS = 300.0
HARD_MAX_VECTOR_DIMENSIONS = 8_192
HARD_MAX_VECTOR_COUNT = 128
HARD_MAX_VECTOR_CELLS = 128 * 8_192
HARD_MAX_VECTOR_CELL_BYTES = 8 * 1024 * 1024
HARD_MAX_INDEX_RECORDS = 8_192
HARD_MAX_TOP_K = 100
HARD_MAX_CANDIDATE_COUNT = 400
HARD_MAX_SMOKE_EXPECTATIONS = 128
HARD_MAX_SMOKE_FAILURES = 256
HARD_MAX_EVIDENCE_COUNT = 128
HARD_MAX_EVIDENCE_TOKENS = 131_072
HARD_MAX_TOKEN_ESTIMATE = 1_000_000
HARD_MAX_WARNINGS = 512
HARD_MAX_ACTIONS = 32
HARD_MAX_PAGE_NUMBER = 1_000_000
HARD_MAX_STREAM_CHUNK_SIZE = 1024 * 1024
HARD_MAX_RESOLVER_ROOTS = 16
HARD_MAX_WORDS_PER_TOKEN = 1_000_000
HARD_MAX_INT64 = (1 << 63) - 1
MIN_INT64 = -(1 << 63)


def _strict_bool(value: object) -> bool:
    if type(value) is not bool:
        raise ValueError("Expected a boolean, got a non-boolean value.")
    return value


def _strict_int(value: object) -> int:
    if type(value) is not int:
        raise ValueError("Expected an integer, got a non-integer value.")
    return value


def _to_finite_float(value: object, *, label: str) -> float:
    """Convert exact int/float to a finite float without overflow."""
    if type(value) is not int and type(value) is not float:
        raise ValueError(f"{label} must be an exact finite number.")
    if type(value) is int:
        try:
            if value.bit_length() > 1023:
                raise ValueError(f"{label} is too large to convert safely.")
        except (AttributeError, RuntimeError, OverflowError, TypeError):
            raise ValueError(f"{label} is too large to convert safely.") from None
    try:
        result = float(value)
    except (AttributeError, RuntimeError, OverflowError, TypeError):
        raise ValueError(f"{label} could not be converted safely.") from None
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite.")
    return result


def _strict_finite_number(value: object) -> float:
    return _to_finite_float(value, label="Number")


def _strict_finite_float_item(value: object) -> float:
    return _to_finite_float(value, label="Vector cell")


StrictBool = Annotated[bool, BeforeValidator(_strict_bool)]
StrictInt = Annotated[int, BeforeValidator(_strict_int)]
StrictFiniteNumber = Annotated[float, BeforeValidator(_strict_finite_number)]
StrictFiniteFloatItem = Annotated[float, BeforeValidator(_strict_finite_float_item)]


def _strict_utc_datetime(value: datetime) -> datetime:
    if type(value) is not datetime:
        raise ValueError("Expected a datetime.")
    try:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Datetime must be timezone-aware.")
        utc = value.astimezone(UTC)
        if not (datetime(2000, 1, 1, tzinfo=UTC) <= utc <= datetime(2100, 1, 1, tzinfo=UTC)):
            raise ValueError("Datetime is outside the supported 2000-2100 UTC range.")
        return utc
    except (AttributeError, RuntimeError, OverflowError, TypeError, ValueError):
        if isinstance(value, datetime) and type(value) is not datetime:
            raise ValueError("Datetime must be an exact datetime instance.") from None
        raise


StrictUtcDatetime = Annotated[datetime, BeforeValidator(_strict_utc_datetime)]


class KnowledgeLimitError(Exception):
    """Base failure for knowledge resource/contract limits."""


class ResourceLimitError(KnowledgeLimitError):
    """Raised when configured knowledge limits are exceeded."""


class DeadlineExceededError(ResourceLimitError):
    """Raised when a bounded knowledge phase exceeds its total deadline."""


class KnowledgeLimits(BaseModel):
    """Conservative frozen low-resource limits with hard caps."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_source_bytes: StrictInt = Field(default=8 * 1024 * 1024, ge=1, le=HARD_MAX_SOURCE_BYTES)
    max_source_count: StrictInt = Field(default=64, ge=1, le=HARD_MAX_SOURCE_COUNT)
    max_section_count: StrictInt = Field(default=512, ge=1, le=HARD_MAX_SECTION_COUNT)
    max_chunk_count: StrictInt = Field(default=4_096, ge=1, le=HARD_MAX_CHUNK_COUNT)
    max_text_bytes: StrictInt = Field(default=1024 * 1024, ge=1, le=HARD_MAX_TEXT_BYTES)
    max_chunk_text_bytes: StrictInt = Field(default=256 * 1024, ge=1, le=HARD_MAX_CHUNK_TEXT_BYTES)
    max_query_bytes: StrictInt = Field(default=8 * 1024, ge=1, le=HARD_MAX_QUERY_BYTES)
    max_path_bytes: StrictInt = Field(default=2_048, ge=1, le=HARD_MAX_PATH_BYTES)
    max_endpoint_bytes: StrictInt = Field(default=2_048, ge=1, le=HARD_MAX_ENDPOINT_BYTES)
    max_key_bytes: StrictInt = Field(default=256, ge=1, le=HARD_MAX_KEY_BYTES)
    max_string_bytes: StrictInt = Field(default=4 * 1024, ge=1, le=HARD_MAX_STRING_BYTES)
    max_credential_bytes: StrictInt = Field(default=512, ge=1, le=HARD_MAX_CREDENTIAL_BYTES)
    max_lines: StrictInt = Field(default=20_000, ge=1, le=HARD_MAX_LINES)
    max_heading_depth: StrictInt = Field(default=8, ge=1, le=HARD_MAX_HEADING_DEPTH)
    max_metadata_items: StrictInt = Field(default=64, ge=1, le=HARD_MAX_METADATA_ITEMS)
    max_metadata_depth: StrictInt = Field(default=4, ge=1, le=HARD_MAX_METADATA_DEPTH)
    max_metadata_retained_bytes: StrictInt = Field(
        default=64 * 1024, ge=1, le=HARD_MAX_METADATA_RETAINED_BYTES
    )
    max_batch_text_count: StrictInt = Field(default=64, ge=1, le=HARD_MAX_BATCH_TEXT_COUNT)
    max_batch_total_bytes: StrictInt = Field(
        default=2 * 1024 * 1024, ge=1, le=HARD_MAX_BATCH_TOTAL_BYTES
    )
    max_request_bytes: StrictInt = Field(default=4 * 1024 * 1024, ge=1, le=HARD_MAX_REQUEST_BYTES)
    max_timeout_seconds: StrictFiniteNumber = Field(default=60.0, gt=0, le=HARD_MAX_TIMEOUT_SECONDS)
    max_vector_dimensions: StrictInt = Field(default=2_048, ge=1, le=HARD_MAX_VECTOR_DIMENSIONS)
    max_vector_count: StrictInt = Field(default=64, ge=1, le=HARD_MAX_VECTOR_COUNT)
    max_vector_cells: StrictInt = Field(default=128 * 1024, ge=1, le=HARD_MAX_VECTOR_CELLS)
    max_vector_cell_bytes: StrictInt = Field(
        default=1024 * 1024, ge=1, le=HARD_MAX_VECTOR_CELL_BYTES
    )
    max_index_records: StrictInt = Field(default=2_048, ge=1, le=HARD_MAX_INDEX_RECORDS)
    max_index_retained_bytes: StrictInt = Field(
        default=4 * 1024 * 1024, ge=1, le=HARD_MAX_INDEX_RETAINED_BYTES
    )
    max_top_k: StrictInt = Field(default=25, ge=1, le=HARD_MAX_TOP_K)
    max_candidate_count: StrictInt = Field(default=200, ge=1, le=HARD_MAX_CANDIDATE_COUNT)
    max_smoke_expectations: StrictInt = Field(default=64, ge=1, le=HARD_MAX_SMOKE_EXPECTATIONS)
    max_smoke_failures: StrictInt = Field(default=128, ge=1, le=HARD_MAX_SMOKE_FAILURES)
    max_evidence_count: StrictInt = Field(default=32, ge=1, le=HARD_MAX_EVIDENCE_COUNT)
    max_evidence_tokens: StrictInt = Field(default=8_192, ge=1, le=HARD_MAX_EVIDENCE_TOKENS)
    max_context_bytes: StrictInt = Field(default=1024 * 1024, ge=1, le=HARD_MAX_CONTEXT_BYTES)
    max_token_estimate: StrictInt = Field(default=100_000, ge=1, le=HARD_MAX_TOKEN_ESTIMATE)
    max_warnings: StrictInt = Field(default=128, ge=1, le=HARD_MAX_WARNINGS)
    max_actions: StrictInt = Field(default=8, ge=1, le=HARD_MAX_ACTIONS)

    @model_validator(mode="after")
    def _validate_cross_field_relationships(self) -> KnowledgeLimits:
        if self.max_query_bytes > self.max_text_bytes:
            raise ValueError("max_query_bytes must not exceed max_text_bytes.")
        if self.max_text_bytes > self.max_source_bytes:
            raise ValueError("max_text_bytes must not exceed max_source_bytes.")
        if self.max_chunk_text_bytes > self.max_text_bytes:
            raise ValueError("max_chunk_text_bytes must not exceed max_text_bytes.")
        if self.max_batch_total_bytes > self.max_request_bytes:
            raise ValueError("max_batch_total_bytes must not exceed max_request_bytes.")
        if self.max_vector_count * self.max_vector_dimensions > self.max_vector_cells:
            raise ValueError(
                "max_vector_cells must cover max_vector_count * max_vector_dimensions."
            )
        if self.max_vector_cells * 8 > self.max_vector_cell_bytes:
            raise ValueError("max_vector_cell_bytes must cover max_vector_cells * 8 bytes.")
        if self.max_top_k > self.max_candidate_count:
            raise ValueError("max_top_k must not exceed max_candidate_count.")
        return self


def utf8_byte_length(value: str, *, max_bytes: int, label: str = "String") -> int:
    """Return UTF-8 byte length, rejecting overflow without whole-encoding."""
    if type(value) is not str:
        raise ResourceLimitError(f"{label} must be a string.")
    if len(value) > max_bytes:
        raise ResourceLimitError(f"{label} exceeds the {max_bytes}-byte UTF-8 limit.")
    encoded = value[:max_bytes].encode("utf-8")
    if len(encoded) > max_bytes:
        raise ResourceLimitError(f"{label} exceeds the {max_bytes}-byte UTF-8 limit.")
    return len(encoded)


def check_utf8_bytes(value: str, *, max_bytes: int, label: str = "String") -> None:
    """Reject strings that exceed a UTF-8 byte budget."""
    utf8_byte_length(value, max_bytes=max_bytes, label=label)


def require_strict_int(value: object, *, label: str) -> int:
    """Validate a public integer argument, rejecting bool and floats."""
    try:
        return _strict_int(value)
    except ValueError:
        raise ResourceLimitError(f"{label} must be an integer.") from None


def require_strict_number(
    value: object,
    *,
    label: str,
    positive: bool = True,
    hard_max: float | None = None,
) -> float:
    """Validate a public numeric argument, rejecting bool and non-finite values."""
    try:
        result = _to_finite_float(value, label=label)
    except ValueError:
        raise ResourceLimitError(f"{label} must be a finite number.") from None
    if positive and result <= 0:
        raise ResourceLimitError(f"{label} must be positive.")
    if hard_max is not None and result > hard_max:
        raise ResourceLimitError(f"{label} exceeds the {hard_max:g} hard limit.")
    return result


def require_finite_number(value: object, *, label: str) -> float:
    """Validate an exact int/float non-bool finite value."""
    try:
        return _to_finite_float(value, label=label)
    except ValueError:
        raise ResourceLimitError(f"{label} must be a finite number.") from None


def resolve_limits(limits: KnowledgeLimits | None) -> KnowledgeLimits:
    """Return an exact KnowledgeLimits instance, rejecting truthy lookalikes."""
    if limits is None:
        return KnowledgeLimits()
    if type(limits) is not KnowledgeLimits:
        raise ResourceLimitError("Limits must be an exact KnowledgeLimits instance.")
    return limits


def validate_deadline_value(deadline: float | None, *, label: str) -> None:
    """Validate an absolute deadline value without reading the clock."""
    if deadline is None:
        return
    try:
        _to_finite_float(deadline, label=label)
    except ValueError:
        raise ResourceLimitError(f"{label} must be a finite number.") from None


def safe_monotonic() -> float:
    """Return a validated finite monotonic clock reading."""
    try:
        reading = time.monotonic()
        result = _to_finite_float(reading, label="Monotonic clock")
    except (TypeError, ValueError, OverflowError):
        raise ResourceLimitError("The monotonic clock returned an invalid reading.") from None
    return result


def make_deadline(
    deadline_seconds: float | None = None,
    *,
    hard_max: float = HARD_MAX_TIMEOUT_SECONDS,
) -> float:
    """Return one absolute monotonic deadline, finite and hard-capped.

    ``None`` uses ``hard_max`` rather than disabling time bounds.  Public
    operations normally pass ``hard_max=active.max_timeout_seconds`` so the
    configured conservative default applies.
    """
    validated_hard_max = require_strict_number(
        hard_max,
        label="Deadline hard maximum",
        hard_max=HARD_MAX_TIMEOUT_SECONDS,
    )
    duration = validated_hard_max if deadline_seconds is None else deadline_seconds
    validated = require_strict_number(
        duration,
        label="Deadline",
        hard_max=validated_hard_max,
    )
    deadline = safe_monotonic() + validated
    if not math.isfinite(deadline):
        raise ResourceLimitError("Deadline computation produced a non-finite value.")
    return deadline


def check_deadline(deadline: float | None, *, label: str = "knowledge phase") -> None:
    """Raise when a shared total deadline has expired."""
    if deadline is None:
        return
    validate_deadline_value(deadline, label="Deadline")
    if safe_monotonic() >= deadline:
        raise DeadlineExceededError(f"{label} exceeded its total deadline.")


def remaining_seconds(deadline: float) -> float:
    """Return the remaining wall time for a deadline, bounded at zero."""
    validate_deadline_value(deadline, label="Deadline")
    remaining = deadline - safe_monotonic()
    if not math.isfinite(remaining):
        raise ResourceLimitError("Remaining deadline computation produced a non-finite value.")
    return max(0.0, remaining)


class RetainedByteBudget:
    """Shared bounded accounting for retained UTF-8 object-graph strings."""

    def __init__(self, max_bytes: int, *, label: str = "Retained content") -> None:
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ResourceLimitError("Retained byte budget must be a positive integer.")
        self.max_bytes = max_bytes
        self.total = 0
        self.label = label

    def add(
        self,
        value: str,
        *,
        max_bytes: int | None = None,
        label: str | None = None,
    ) -> int:
        per_value = self.max_bytes if max_bytes is None else max_bytes
        if type(per_value) is not int or per_value <= 0:
            raise ResourceLimitError("Per-value byte budget must be a positive integer.")
        size = utf8_byte_length(value, max_bytes=per_value, label=label or self.label)
        new_total = self.total + size
        if new_total > self.max_bytes:
            raise ResourceLimitError(
                f"{self.label} exceeds the {self.max_bytes}-byte aggregate UTF-8 limit."
            )
        self.total = new_total
        return size


class VectorBudget:
    """Shared bounded accounting for retained vector cells and bytes."""

    def __init__(
        self,
        *,
        max_cells: int,
        max_bytes: int,
        label: str = "Vector data",
    ) -> None:
        if type(max_cells) is not int or max_cells <= 0:
            raise ResourceLimitError("Vector cell budget must be a positive integer.")
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ResourceLimitError("Vector byte budget must be a positive integer.")
        self.max_cells = max_cells
        self.max_bytes = max_bytes
        self.cells = 0
        self.bytes_used = 0
        self.label = label

    def add(
        self,
        vector: tuple[object, ...] | list[object],
        *,
        dimensions: int,
    ) -> None:
        if type(dimensions) is not int or dimensions < 0:
            raise ResourceLimitError("Vector dimensions must be a non-negative integer.")
        if type(vector) not in (tuple, list):
            raise ResourceLimitError("Vector data must be an exact builtin tuple or list.")
        if len(vector) != dimensions:
            raise ResourceLimitError("Vector dimensions do not match the vector length.")
        new_cells = self.cells + dimensions
        new_bytes = self.bytes_used + dimensions * 8
        if new_cells > self.max_cells:
            raise ResourceLimitError(
                f"{self.label} exceeds the {self.max_cells}-cell aggregate limit."
            )
        if new_bytes > self.max_bytes:
            raise ResourceLimitError(
                f"{self.label} exceeds the {self.max_bytes}-byte aggregate limit."
            )
        self.cells = new_cells
        self.bytes_used = new_bytes
