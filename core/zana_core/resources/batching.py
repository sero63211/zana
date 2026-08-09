"""Bounded streaming/batching helpers that never materialize whole inputs."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from typing import Any

from pydantic import Field

from zana_core.resources.models import (
    MAX_SAFE_BYTES,
    CategoryLimit,
    OperationCategory,
    OperationRequest,
    _Frozen,
)

SizeProvider = Callable[[Any], int]


class BatchLimitError(ValueError):
    """Raised when a batch would exceed its declared bounded limits."""


class BatchPlan(_Frozen):
    """Deterministic bounded batch plan derived from a request and policy."""

    category: OperationCategory
    max_items_per_batch: int = Field(ge=1)
    max_bytes_per_batch: int = Field(ge=1)
    declared_items: int | None = Field(default=None, ge=0, le=MAX_SAFE_BYTES)
    declared_bytes: int | None = Field(default=None, ge=0, le=MAX_SAFE_BYTES)
    max_total_items: int | None = Field(default=None, ge=0, le=MAX_SAFE_BYTES)


def _size_of(item: Any) -> int:
    if isinstance(item, bytes | bytearray | memoryview):
        return len(item)
    if isinstance(item, str):
        return len(item.encode("utf-8"))
    try:
        return int(len(item))
    except TypeError:
        return 1


def plan_batch(
    request: OperationRequest,
    limit: CategoryLimit,
    *,
    size_provider: SizeProvider | None = None,
) -> BatchPlan:
    """Build a bounded batch plan from a request and its category limit."""
    del size_provider
    max_items = limit.max_items
    max_bytes = limit.max_bytes
    if max_items is None:
        max_items = 1000 if request.items_count is None else max(1, request.items_count)
    if max_bytes is None:
        max_bytes = 1 << 20
    if max_items < 1 or max_bytes < 1:
        raise BatchLimitError("batch limits must be positive")
    declared_items = request.items_count
    declared_bytes = request.byte_count
    max_total = limit.max_items
    return BatchPlan(
        category=request.category,
        max_items_per_batch=max_items,
        max_bytes_per_batch=max_bytes,
        declared_items=declared_items,
        declared_bytes=declared_bytes,
        max_total_items=max_total,
    )


def validate_batch_limits(request: OperationRequest, limit: CategoryLimit) -> list[str]:
    """Return structured violations before any iteration starts."""
    violations: list[str] = []
    if (
        request.items_count is not None
        and limit.max_items is not None
        and request.items_count > limit.max_items
    ):
        violations.append(
            f"items_count {request.items_count} exceeds category cap {limit.max_items}"
        )
    if (
        request.byte_count is not None
        and limit.max_bytes is not None
        and request.byte_count > limit.max_bytes
    ):
        violations.append(f"byte_count {request.byte_count} exceeds category cap {limit.max_bytes}")
    return violations


def iter_batches(
    iterable: Iterable[Any],
    *,
    max_items: int,
    max_bytes: int,
    max_total_items: int | None = None,
    size_provider: SizeProvider | None = None,
) -> Iterator[tuple[tuple[Any, ...], int]]:
    """Yield bounded batches over an iterable without materializing it all.

    Batches are capped by item count and byte count. A single item larger
    than ``max_bytes`` raises immediately instead of growing unboundedly.
    Items are referenced, never copied, so large byte buffers are not
    duplicated.
    """
    if max_items < 1 or max_bytes < 1:
        raise BatchLimitError("max_items and max_bytes must be positive")
    size_of = size_provider or _size_of
    batch: list[Any] = []
    batch_bytes = 0
    for total_items, item in enumerate(iterable, start=1):
        size = size_of(item)
        if size < 0:
            raise BatchLimitError("item size provider returned a negative value")
        if size > max_bytes:
            raise BatchLimitError(
                f"single item size {size} exceeds max_bytes {max_bytes}; "
                "failing before unbounded growth"
            )
        if max_total_items is not None and total_items > max_total_items:
            raise BatchLimitError(f"iterable exceeds max_total_items {max_total_items}")
        if batch and (len(batch) >= max_items or batch_bytes + size > max_bytes):
            yield (tuple(batch), batch_bytes)
            batch = []
            batch_bytes = 0
        batch.append(item)
        batch_bytes += size
    if batch:
        yield (tuple(batch), batch_bytes)
