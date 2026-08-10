"""Bounded local observability registry and sink health surface."""

from __future__ import annotations

import re
import threading
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from zana_core.observability.events import Event
from zana_core.observability.serialization import MAX_ENCODED_LINE_BYTES, serialize_event
from zana_core.observability.sinks import (
    BoundedMemorySink,
    LocalJsonlSink,
    SinkStats,
    WriteResult,
    _safe_event_id,
)
from zana_core.streaming.redaction import redact_value

MAX_RETAINED_EVENTS = 500
MAX_RETAINED_EVENTS_HARD_CAP = 1000
MAX_RETAINED_BYTES_DEFAULT = 2 * 1024 * 1024
MAX_RETAINED_BYTES_HARD_CAP = 16 * 1024 * 1024
MAX_EVENT_PAGE_LIMIT = 200
_JSONL_ERROR_REASONS = frozenset({"PLATFORM_UNSUPPORTED"})
_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
_SENSITIVE_LOOKALIKE = frozenset(
    {
        "bearer",
        "bearertoken",
        "token",
        "tokens",
        "secret",
        "secrets",
        "password",
        "apikey",
        "accesstoken",
        "authorization",
        "credential",
        "credentials",
    }
)
_IDENTIFIER_DIGEST_SALT = "zana-event-identifier-v1"

Clock = Callable[[], datetime]


class _StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class RetainedEvent(_StrictModel):
    """One already-redacted bounded event retained by the registry."""

    sequence: int = Field(ge=0)
    event_id: str = Field(max_length=128)
    line: str = Field(max_length=MAX_ENCODED_LINE_BYTES + 16)
    bytes: int = Field(ge=0)
    received_at: datetime


class EventPage(_StrictModel):
    """One bounded descending page of retained redacted events."""

    items: tuple[RetainedEvent, ...]
    count: int = Field(ge=0)
    limit: int = Field(ge=1)
    next_cursor: int | None = Field(default=None, ge=0)
    truncated: bool = False
    total_available: int = Field(ge=0)
    retention_dropped: int = Field(ge=0)
    retention_dropped_bytes: int = Field(ge=0)
    max_retained_bytes: int = Field(ge=1)
    retained_bytes: int = Field(ge=0)


class MemorySinkHealth(_StrictModel):
    present: bool
    max_events: int = Field(default=0, ge=0)
    max_bytes: int = Field(default=0, ge=0)
    retained_events: int = Field(default=0, ge=0)
    retained_bytes: int = Field(default=0, ge=0)
    stats: SinkStats = Field(default_factory=SinkStats)


class JsonlSinkHealth(_StrictModel):
    present: bool = False
    available: bool = False
    reason: str | None = Field(default=None, max_length=64)
    max_bytes: int | None = Field(default=None, ge=0)
    max_retention: int | None = Field(default=None, ge=0)
    filename: str | None = Field(default=None, max_length=128)
    log_root: str | None = Field(default=None, max_length=512)
    stats: SinkStats = Field(default_factory=SinkStats)


class ObservabilityHealth(_StrictModel):
    telemetry_enabled: bool = False
    remote_transport: str = "none"
    mode: str = Field(min_length=1, max_length=64)
    memory: MemorySinkHealth
    jsonl: JsonlSinkHealth
    total: SinkStats = Field(default_factory=SinkStats)
    max_retained_events: int = Field(ge=1)
    max_retained_bytes: int = Field(ge=1)
    retained_events: int = Field(ge=0)
    retained_bytes: int = Field(ge=0)
    retention_dropped: int = Field(ge=0)
    retention_dropped_bytes: int = Field(ge=0)
    failures: int = Field(ge=0)
    partial_deliveries: int = Field(ge=0)
    closed: bool = False


class ObservabilityRegistry:
    """Thread-safe bounded local event registry.

    Writes go only to injected local sinks, records are retained only after at
    least one sink accepts the redacted event, and no remote transport,
    telemetry, sampler, or background thread exists in this registry.
    """

    def __init__(
        self,
        memory_sink: BoundedMemorySink | None = None,
        jsonl_sink: LocalJsonlSink | None = None,
        *,
        jsonl_error: str | None = None,
        max_retained_events: int = MAX_RETAINED_EVENTS,
        max_retained_bytes: int = MAX_RETAINED_BYTES_DEFAULT,
        now: Clock | None = None,
    ) -> None:
        if memory_sink is not None and type(memory_sink) is not BoundedMemorySink:
            raise TypeError("memory_sink must be an exact BoundedMemorySink or None")
        if jsonl_sink is not None and type(jsonl_sink) is not LocalJsonlSink:
            raise TypeError("jsonl_sink must be an exact LocalJsonlSink or None")
        _require_safe_config(
            max_retained_events=max_retained_events,
            max_retained_bytes=max_retained_bytes,
            now=now,
        )
        if jsonl_error is not None:
            if jsonl_sink is not None or type(jsonl_error) is not str:
                raise ValueError("jsonl_error requires an exact reason and no jsonl_sink")
            if jsonl_error not in _JSONL_ERROR_REASONS:
                raise ValueError("jsonl_error must be a supported exact reason")
        self._memory = memory_sink
        self._jsonl = jsonl_sink
        self._jsonl_error = jsonl_error
        self._max_retained_events = max_retained_events
        self._max_retained_bytes = max_retained_bytes
        self._now = now or (lambda: datetime.now(UTC))
        self._records: deque[RetainedEvent] = deque()
        self._retained_bytes = 0
        self._sequence = 0
        self._retention_drops = 0
        self._retention_drop_bytes = 0
        self._failures = 0
        self._partial_deliveries = 0
        self._closed = False
        self._lock = threading.RLock()

    def write(self, event: Event) -> WriteResult:
        """Write one exact Event to configured local sinks and retain it."""
        with self._lock:
            if type(event) is not Event:
                self._failures += 1
                return WriteResult(ok=False, event_id="", error="WRITE_REJECTED")
            if self._closed:
                self._failures += 1
                return WriteResult(ok=False, event_id="", error="REGISTRY_CLOSED")
            event_id = ""
            try:
                safe_event = _sanitize_event(event)
                line = serialize_event(safe_event)
            except TypeError:
                self._failures += 1
                return WriteResult(ok=False, event_id=event_id, error="WRITE_REJECTED")
            except Exception:
                self._failures += 1
                return WriteResult(ok=False, event_id=event_id, error="WRITE_FAILED")
            event_id = _safe_event_id(safe_event)
            children: list[object] = []
            if self._memory is not None:
                children.append(self._memory)
            if self._jsonl is not None:
                children.append(self._jsonl)
            if not children:
                self._failures += 1
                return WriteResult(ok=False, event_id=event_id, error="NO_SINKS_CONFIGURED")
            failures = 0
            for child in children:
                try:
                    result = child.write(safe_event)  # type: ignore[attr-defined]
                except Exception:
                    failures += 1
                    continue
                if type(result) is not WriteResult or not result.ok:
                    failures += 1
            if failures == len(children):
                self._failures += 1
                return WriteResult(ok=False, event_id=event_id, error="ALL_SINKS_FAILED")
            if failures:
                self._partial_deliveries += 1
                result_error = "PARTIAL_DELIVERY"
            else:
                result_error = None
            self._sequence += 1
            size = len(line.encode("utf-8"))
            record = RetainedEvent(
                sequence=self._sequence,
                event_id=event_id,
                line=line,
                bytes=size,
                received_at=self._now(),
            )
            self._records.append(record)
            self._retained_bytes += size
            while len(self._records) > self._max_retained_events or (
                self._retained_bytes > self._max_retained_bytes
            ):
                removed = self._records.popleft()
                self._retained_bytes -= removed.bytes
                self._retention_drops += 1
                self._retention_drop_bytes += removed.bytes
            return WriteResult(ok=True, event_id=event_id, error=result_error)

    def events(
        self,
        *,
        before_sequence: int | None = None,
        limit: int = 50,
    ) -> EventPage:
        """Return one bounded descending page of retained events.

        ``before_sequence`` is an exclusive cursor: only records with a smaller
        sequence are returned, so clients page toward older records.
        """
        with self._lock:
            if type(limit) is not int or limit < 1 or limit > MAX_EVENT_PAGE_LIMIT:
                raise ValueError("limit must be an exact int within the event page cap")
            if before_sequence is not None and (
                type(before_sequence) is not int or before_sequence < 0
            ):
                raise ValueError("before_sequence must be a non-negative exact int or None")
            records = tuple(self._records)
            retention_dropped = self._retention_drops
            if before_sequence is not None:
                records = tuple(record for record in records if record.sequence < before_sequence)
            total = len(records)
            newest = records[-limit:]
            page = tuple(reversed(newest))
            truncated = len(records) > len(page)
            next_cursor = page[-1].sequence if truncated and page else None
            return EventPage(
                items=page,
                count=len(page),
                limit=limit,
                next_cursor=next_cursor,
                truncated=truncated,
                total_available=total,
                retention_dropped=retention_dropped,
                retention_dropped_bytes=self._retention_drop_bytes,
                max_retained_bytes=self._max_retained_bytes,
                retained_bytes=self._retained_bytes,
            )

    def health(self) -> ObservabilityHealth:
        """Return live sink and retention health without any remote transport."""
        with self._lock:
            closed = self._closed
            if self._memory is not None:
                memory = MemorySinkHealth(
                    present=True,
                    max_events=self._memory.max_events,
                    max_bytes=self._memory.max_bytes,
                    retained_events=self._memory.event_count(),
                    retained_bytes=self._memory.held_bytes(),
                    stats=_safe_stats(self._memory.stats),
                )
            else:
                memory = MemorySinkHealth(present=False)
            if self._jsonl is not None:
                jsonl_available = not closed
                jsonl = JsonlSinkHealth(
                    present=True,
                    available=jsonl_available,
                    reason=None if jsonl_available else "CLOSED",
                    max_bytes=self._jsonl.max_bytes,
                    max_retention=self._jsonl.max_retention,
                    filename=self._jsonl.filename,
                    log_root=_safe_path(str(self._jsonl.root)),
                    stats=_safe_stats(self._jsonl.stats),
                )
            else:
                jsonl = JsonlSinkHealth(
                    present=False,
                    available=False,
                    reason=self._jsonl_error if self._jsonl_error is not None else "NOT_CONFIGURED",
                )
            if self._memory is not None and self._jsonl is not None:
                mode = "local_memory_jsonl"
            elif self._memory is not None:
                mode = "local_memory"
            elif self._jsonl is not None:
                mode = "local_jsonl"
            else:
                mode = "disabled"
            return ObservabilityHealth(
                telemetry_enabled=False,
                remote_transport="none",
                mode=mode,
                memory=memory,
                jsonl=jsonl,
                total=_sum_stats(memory.stats, jsonl.stats),
                max_retained_events=self._max_retained_events,
                max_retained_bytes=self._max_retained_bytes,
                retained_events=len(self._records),
                retained_bytes=self._retained_bytes,
                retention_dropped=self._retention_drops,
                retention_dropped_bytes=self._retention_drop_bytes,
                failures=self._failures,
                partial_deliveries=self._partial_deliveries,
                closed=closed,
            )

    def close(self) -> None:
        """Idempotently close the registry and its owned JSONL sink."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._jsonl is not None:
                try:
                    self._jsonl.close()
                except Exception:
                    self._failures += 1


def _safe_stats(stats: Callable[[], SinkStats]) -> SinkStats:
    try:
        value = stats()
    except Exception:
        return SinkStats(failures=1)
    if type(value) is not SinkStats:
        return SinkStats(failures=1)
    return value


def _sum_stats(*stats: SinkStats) -> SinkStats:
    return SinkStats(
        events_written=sum(stat.events_written for stat in stats),
        events_dropped=sum(stat.events_dropped for stat in stats),
        bytes_written=sum(stat.bytes_written for stat in stats),
        failures=sum(stat.failures for stat in stats),
    )


def _safe_path(value: str) -> str:
    if not value:
        return ""
    redacted = redact_value({"log_root": value})
    result = redacted.get("log_root")
    return result if type(result) is str else ""


def safe_public_identifier(value: str) -> str:
    """Return a bounded nonsecret identifier or a stable redacted reference."""
    if type(value) is not str:
        return ""
    if not value:
        return ""
    if len(value) > 128:
        return _identifier_reference(value if type(value) is str else "")
    lowered = value.lower()
    if any(character in value for character in ("/", "\\")):
        return _identifier_reference(value)
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return _identifier_reference(value)
    normalized = re.sub(r"[^a-z0-9]", "", lowered)
    if any(marker in normalized for marker in _SENSITIVE_LOOKALIKE):
        return _identifier_reference(value)
    if not _SAFE_IDENTIFIER_RE.fullmatch(value):
        return _identifier_reference(value)
    return value


def _sanitize_event(event: Event) -> Event:
    """Return one exact Event with every public identifier field sanitized.

    The same sanitized instance is passed to sinks, serialization, and the
    returned event id, so raw identifiers never reach memory, JSONL, or API.
    """
    safe_context = event.context.model_copy(
        update={
            "operation_id": safe_public_identifier(event.context.operation_id),
            "job_id": safe_public_identifier(event.context.job_id),
            "phase": safe_public_identifier(event.context.phase),
            "instance_id": (
                safe_public_identifier(event.context.instance_id)
                if event.context.instance_id is not None
                else None
            ),
            "image_digest": (
                safe_public_identifier(event.context.image_digest)
                if event.context.image_digest is not None
                else None
            ),
        }
    )
    redacted = event.model_copy(
        update={
            "operation_id": safe_public_identifier(event.operation_id),
            "job_id": safe_public_identifier(event.job_id),
            "phase": safe_public_identifier(event.phase),
            "recovery_code": (
                safe_public_identifier(event.recovery_code)
                if event.recovery_code is not None
                else None
            ),
            "context": safe_context,
        }
    )
    return redacted


def _identifier_reference(value: str) -> str:
    digest = sha256((_IDENTIFIER_DIGEST_SALT + value).encode("utf-8", errors="replace")).hexdigest()
    return f"redacted-{digest[:16]}"


def _require_safe_config(
    *,
    max_retained_events: object,
    max_retained_bytes: object,
    now: Any,
) -> None:
    if type(max_retained_events) is not int or not (
        1 <= max_retained_events <= MAX_RETAINED_EVENTS_HARD_CAP
    ):
        raise ValueError("max_retained_events must be within the hard cap")
    if type(max_retained_bytes) is not int or not (
        1 <= max_retained_bytes <= MAX_RETAINED_BYTES_HARD_CAP
    ):
        raise ValueError("max_retained_bytes must be within the hard cap")
    if now is not None and not callable(now):
        raise TypeError("now must be callable or None")
