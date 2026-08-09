"""Bounded synchronous drain over an injected EventSource protocol."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from zana_core.streaming.encoder import SSEEncoder
from zana_core.streaming.models import (
    ErrorMetadata,
    EventBatch,
    EventCursor,
    StreamLimits,
)


class DrainCancelledError(ValueError):
    """The drain was cancelled before completing."""


class DrainTimeoutError(ValueError):
    """The drain exceeded the injected deadline."""


class StreamOrderError(ValueError):
    """A batch cursor violated monotonic sequence ordering."""


class EventSource(Protocol):
    """Injected source that reads one bounded batch after a cursor."""

    def read_batch(self, cursor: EventCursor) -> EventBatch | None: ...


class DrainResult(BaseModel):
    """Bounded drain outcome; events are emitted, never accumulated."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    events_emitted: int = 0
    batches_read: int = 0
    bytes_emitted: int = 0
    final_cursor: EventCursor
    ended: bool = False
    cancelled: bool = False
    timed_out: bool = False
    error: ErrorMetadata | None = None


def drain(
    source: EventSource,
    *,
    cursor: EventCursor,
    limits: StreamLimits | None = None,
    on_event: Callable[[object], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    clock: Callable[[], float] | None = None,
    max_duration_seconds: float | None = None,
    encoder: SSEEncoder | None = None,
) -> DrainResult:
    """Drain bounded batches synchronously without background work.

    No threads, tasks, sleep, polling loops, timers, queues, or telemetry are
    created. An empty source terminates honestly. A source that returns no
    events is treated as empty.
    """
    resolved_limits = limits or StreamLimits()
    resolved_encoder = encoder or SSEEncoder(resolved_limits)
    clock_fn = clock or _monotonic
    is_cancelled = cancelled or (lambda: False)
    duration = (
        max_duration_seconds
        if max_duration_seconds is not None
        else resolved_limits.max_duration_seconds
    )
    if duration <= 0:
        raise ValueError("max_duration_seconds must be positive")

    started = clock_fn()
    current = cursor
    batches = 0
    events = 0
    total_bytes = 0

    while True:
        if is_cancelled():
            return DrainResult(
                events_emitted=events,
                batches_read=batches,
                bytes_emitted=total_bytes,
                final_cursor=current,
                ended=False,
                cancelled=True,
            )
        elapsed = clock_fn() - started
        if elapsed >= duration:
            return DrainResult(
                events_emitted=events,
                batches_read=batches,
                bytes_emitted=total_bytes,
                final_cursor=current,
                ended=False,
                timed_out=True,
            )
        if batches >= resolved_limits.max_batches:
            return DrainResult(
                events_emitted=events,
                batches_read=batches,
                bytes_emitted=total_bytes,
                final_cursor=current,
                ended=False,
                error=ErrorMetadata(
                    code="STREAM_BATCH_LIMIT",
                    message="batch limit reached before stream end",
                    recovery_action="Resume from the returned cursor.",
                ),
            )

        try:
            batch = source.read_batch(current)
        except Exception:  # noqa: BLE001 - source boundary must never leak exceptions
            return DrainResult(
                events_emitted=events,
                batches_read=batches,
                bytes_emitted=total_bytes,
                final_cursor=current,
                ended=False,
                error=ErrorMetadata(
                    code="SOURCE_ERROR",
                    message="event source failed while reading a batch",
                    recovery_action="Retry from the returned cursor.",
                ),
            )

        if batch is None:
            return DrainResult(
                events_emitted=events,
                batches_read=batches,
                bytes_emitted=total_bytes,
                final_cursor=current,
                ended=True,
            )
        if batch.cursor.source_id != current.source_id:
            return DrainResult(
                events_emitted=events,
                batches_read=batches,
                bytes_emitted=total_bytes,
                final_cursor=current,
                ended=False,
                error=ErrorMetadata(
                    code="STREAM_SOURCE_MISMATCH",
                    message="batch source changed; cursor is no longer valid",
                    recovery_action="Restart from the initial source cursor.",
                ),
            )
        if batch.cursor.sequence < current.sequence:
            return DrainResult(
                events_emitted=events,
                batches_read=batches,
                bytes_emitted=total_bytes,
                final_cursor=current,
                ended=False,
                error=ErrorMetadata(
                    code="STREAM_CURSOR_STALE",
                    message="batch cursor moved backwards; duplication or reordering detected",
                    recovery_action="Resume from the current stream cursor.",
                ),
            )
        if (
            batch.cursor.sequence > current.sequence
            and batch.cursor.sequence != current.sequence + len(batch.events)
        ):
            return DrainResult(
                events_emitted=events,
                batches_read=batches,
                bytes_emitted=total_bytes,
                final_cursor=current,
                ended=False,
                error=ErrorMetadata(
                    code="STREAM_CURSOR_GAP",
                    message="batch cursor implies missing events",
                    recovery_action="Resume from the current stream cursor.",
                ),
            )

        batches += 1
        for event in batch.events:
            if events >= resolved_limits.max_batch_events:
                return DrainResult(
                    events_emitted=events,
                    batches_read=batches,
                    bytes_emitted=total_bytes,
                    final_cursor=current,
                    ended=False,
                    error=ErrorMetadata(
                        code="STREAM_EVENT_LIMIT",
                        message="event cap reached before stream end",
                        recovery_action="Resume from the returned cursor.",
                    ),
                )
            if events >= resolved_limits.max_batch_events:
                break
            try:
                chunk = resolved_encoder.encode(event)
            except Exception:  # noqa: BLE001 - encode boundary is typed
                return DrainResult(
                    events_emitted=events,
                    batches_read=batches,
                    bytes_emitted=total_bytes,
                    final_cursor=current,
                    ended=False,
                    error=ErrorMetadata(
                        code="STREAM_ENCODE_ERROR",
                        message="event could not be encoded within stream limits",
                        recovery_action="Retry from the returned cursor after fixing the event.",
                    ),
                )
            total_bytes += len(chunk)
            if on_event is not None:
                on_event(event)
            events += 1
            current = current.next()

        if batch.error is not None:
            return DrainResult(
                events_emitted=events,
                batches_read=batches,
                bytes_emitted=total_bytes,
                final_cursor=current,
                ended=True,
                error=batch.error,
            )
        if batch.terminal:
            return DrainResult(
                events_emitted=events,
                batches_read=batches,
                bytes_emitted=total_bytes,
                final_cursor=current,
                ended=True,
            )
        if not batch.events:
            return DrainResult(
                events_emitted=events,
                batches_read=batches,
                bytes_emitted=total_bytes,
                final_cursor=current,
                ended=True,
            )


def _monotonic() -> float:
    import time

    return time.monotonic()
