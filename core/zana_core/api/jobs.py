"""Authenticated generic job and bounded SSE event endpoints."""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from contextlib import suppress
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import StreamingResponse

from zana_core.api.deps import UnitOfWorkDep, verify_token
from zana_core.api.errors import http_error
from zana_core.api.schemas import JobRead
from zana_core.db.models import Job
from zana_core.db.repositories import JobEventStreamRow
from zana_core.jobs.services import MAX_EVENT_PAGE_SIZE, JobNotFoundError, JobService
from zana_core.streaming.encoder import SSEEncoder, StreamEncodeError, StreamLimitError
from zana_core.streaming.models import (
    EventCursor,
    EventKind,
    InvalidCursorError,
    StreamEvent,
    StreamLimits,
)
from zana_core.streaming.redaction import RedactionLimits, Redactor

DEFAULT_EVENT_PAGE_SIZE = 50
MAX_MESSAGE_BYTES = 1024
MAX_PHASE_BYTES = 96
MAX_ERROR_BYTES = 1024
MAX_EVENT_DATA_BYTES = 2048
MAX_EVENT_BYTES = 4096
TERMINAL_ERROR_BYTES = 2048
MAX_PRIMARY_TOTAL_BYTES = MAX_EVENT_PAGE_SIZE * MAX_EVENT_BYTES
MAX_STREAM_TOTAL_BYTES = MAX_PRIMARY_TOTAL_BYTES + TERMINAL_ERROR_BYTES
MAX_CURSOR_HEADER_BYTES = 256
_TRUNCATION_MARKER = "...[truncated]"
_INVALID_TEXT_MARKER = "...[invalid-text]"

router = APIRouter(
    prefix="/api/v1/jobs",
    tags=["jobs"],
    dependencies=[Depends(verify_token)],
)


@router.get("/{job_id}", response_model=JobRead)
def get_job(job_id: int, uow: UnitOfWorkDep) -> Job:
    try:
        return JobService(uow).get_job(job_id)
    except JobNotFoundError:
        raise http_error(
            404,
            "JOB_NOT_FOUND",
            "No job exists with this id.",
            actions=["list_capabilities"],
        ) from None


@router.get("/{job_id}/events")
def stream_job_events(
    job_id: int,
    uow: UnitOfWorkDep,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    limit: int = Query(default=DEFAULT_EVENT_PAGE_SIZE, ge=1, le=MAX_EVENT_PAGE_SIZE),
) -> StreamingResponse:
    """Stream one bounded page of persisted job events as SSE.

    ``Last-Event-ID`` is interpreted as the numeric database event id; plain
    integers and the canonical ``jobs:<sequence>`` form are accepted. Invalid
    cursors return a deterministic 400; a cursor ahead of the current page
    yields an honest empty page with no replay. Reconnect with the last
    received SSE id to continue.
    """
    after_event_id = _parse_resume_cursor(last_event_id)
    service = JobService(uow)
    try:
        rows = service.list_event_stream_rows(
            job_id,
            after_event_id=after_event_id,
            limit=limit,
        )
    except JobNotFoundError:
        raise http_error(
            404,
            "JOB_NOT_FOUND",
            "No job exists with this id.",
            actions=["list_capabilities"],
        ) from None

    dto_events = tuple(_job_event_to_dto(row) for row in rows)
    encoder = SSEEncoder(
        StreamLimits(
            max_data_bytes=MAX_EVENT_DATA_BYTES,
            max_event_bytes=MAX_EVENT_BYTES,
            max_total_bytes=MAX_PRIMARY_TOTAL_BYTES,
        ),
    )
    emergency_encoder = SSEEncoder(
        StreamLimits(
            max_data_bytes=MAX_EVENT_DATA_BYTES,
            max_event_bytes=MAX_EVENT_BYTES,
            max_total_bytes=TERMINAL_ERROR_BYTES,
        )
    )

    def stream() -> Iterator[bytes]:
        for dto in dto_events:
            try:
                yield encoder.encode(dto)
            except (StreamEncodeError, StreamLimitError):
                with suppress(StreamEncodeError, StreamLimitError):
                    yield emergency_encoder.encode(_terminal_error_event(dto))
                return

    headers = {
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers=headers,
    )


def _parse_resume_cursor(last_event_id: str | None) -> int:
    """Parse Last-Event-ID into a database event id offset."""
    if last_event_id is None:
        return 0
    if type(last_event_id) is not str:
        raise http_error(
            400,
            "INVALID_STREAM_CURSOR",
            "Last-Event-ID must be a string.",
            recoverable=True,
            actions=["retry_without_cursor"],
        )
    if last_event_id == "":
        return 0
    if len(last_event_id) > MAX_CURSOR_HEADER_BYTES:
        raise http_error(
            400,
            "INVALID_STREAM_CURSOR",
            "Last-Event-ID exceeds the maximum cursor length.",
            recoverable=True,
            actions=["retry_without_cursor"],
        )
    candidate = last_event_id[:MAX_CURSOR_HEADER_BYTES]
    if len(candidate.encode("utf-8")) > MAX_CURSOR_HEADER_BYTES:
        raise http_error(
            400,
            "INVALID_STREAM_CURSOR",
            "Last-Event-ID exceeds the maximum cursor length.",
            recoverable=True,
            actions=["retry_without_cursor"],
        )
    try:
        cursor = EventCursor.parse(
            candidate,
            default_source="jobs",
        )
    except InvalidCursorError:
        raise http_error(
            400,
            "INVALID_STREAM_CURSOR",
            "Last-Event-ID must be a non-negative event id.",
            recoverable=True,
            actions=["retry_without_cursor"],
        ) from None
    if cursor.source_id != "jobs":
        raise http_error(
            400,
            "INVALID_STREAM_SOURCE",
            "Last-Event-ID source must be 'jobs'.",
            recoverable=True,
            actions=["retry_with_jobs_cursor"],
        )
    return cursor.sequence


def _safe_event_id(value: object) -> str:
    """Return a bounded canonical SSE id for an exact non-negative int."""
    if type(value) is int and value >= 0:
        return str(value)
    return "error"


def _safe_job_id(value: object) -> int | None:
    """Return an exact non-negative int only; hostile values become None."""
    if type(value) is int and value >= 0:
        return value
    return None


def _job_event_to_dto(row: JobEventStreamRow) -> StreamEvent:
    """Convert a bounded SQL projection into a deterministic safe SSE event."""
    if type(row) is not JobEventStreamRow:
        return StreamEvent(
            name=EventKind.JOB_ERROR,
            id="error",
            data={
                "error": {
                    "code": "INVALID_PROJECTION",
                    "message": "job event projection is not a bounded row",
                    "recoverable": True,
                }
            },
            terminal=True,
        )
    return StreamEvent(
        name=_event_kind(row.kind),
        id=_safe_event_id(row.id),
        data=_safe_payload(row),
        terminal=False,
    )


def _event_kind(kind: str) -> EventKind:
    if type(kind) is not str:
        return EventKind.ERROR
    return {
        "CREATED": EventKind.JOB_CREATED,
        "STATUS_CHANGED": EventKind.JOB_STATUS,
        "PROGRESS": EventKind.JOB_PROGRESS,
        "ERROR": EventKind.JOB_ERROR,
        "CANCELLED": EventKind.JOB_CANCELLED,
    }.get(kind, EventKind.ERROR)


def _safe_payload(row: JobEventStreamRow) -> dict[str, Any]:
    return {
        "job_id": _safe_job_id(row.job_id),
        "kind": _safe_kind(row.kind),
        "phase": _bounded_text(row.phase, MAX_PHASE_BYTES),
        "message": _bounded_text(row.message, MAX_MESSAGE_BYTES),
        "progress_0_1": _safe_progress(row.progress_0_1),
        "error": _bounded_error_string(row.error_json),
        "created_at": _bounded_iso8601(row.created_at),
    }


def _safe_kind(kind: str) -> str:
    """Normalize raw DB kind to a small canonical value."""
    return _event_kind(kind).value


def _bounded_text(value: str, max_bytes: int) -> str:
    """Truncate untrusted text with one bounded UTF-8 pass.

    Retains at most ``max_bytes`` bytes, stops as soon as the cap is
    exceeded, never encodes or materializes the whole input, and never splits
    a UTF-8 code point.
    """
    if type(value) is not str or type(max_bytes) is not int or max_bytes <= 0:
        return _INVALID_TEXT_MARKER
    marker = _TRUNCATION_MARKER
    marker_bytes = len(marker.encode("utf-8"))
    budget = max(0, max_bytes - marker_bytes)
    retained: list[str] = []
    total = 0
    for character in value:
        encoded_char = character.encode("utf-8")
        if total + len(encoded_char) > budget:
            break
        retained.append(character)
        total += len(encoded_char)
    result = "".join(retained)
    if result == value:
        return value
    return result + marker


def _bounded_error_bytes(value: dict[str, Any]) -> dict[str, Any]:
    if type(value) is not dict:
        return {"code": "REDACTED_ERROR", "message": "...[truncated]"}
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    if len(encoded) <= MAX_ERROR_BYTES:
        return value
    return {
        "code": "REDACTED_ERROR",
        "message": _bounded_text("error details exceeded the byte budget", 160),
        "...truncated": True,
    }


def _bounded_error_string(value: str | None) -> dict[str, Any] | None:
    """Parse only the bounded SQL error projection; invalid input is sentinel."""
    if value is None:
        return None
    if type(value) is not str:
        return {"code": "REDACTED_ERROR", "message": "...[truncated]"}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {"code": "REDACTED_ERROR", "message": "...[truncated]"}
    if type(parsed) is not dict:
        return {"code": "REDACTED_ERROR", "message": "...[truncated]"}
    parsed = Redactor(RedactionLimits(max_items=16, max_depth=8, max_string_length=64)).redact(
        parsed
    )
    if type(parsed) is not dict:
        return {"code": "REDACTED_ERROR", "message": "...[truncated]"}
    return _bounded_error_bytes(parsed)


def _safe_progress(value: float) -> float | None:
    """Normalize hostile progress to finite [0,1] or None."""
    if type(value) is int:
        finite = float(value)
    elif type(value) is float:
        finite = value
    else:
        return None
    if not math.isfinite(finite) or finite < 0.0 or finite > 1.0:
        return None
    return finite


def _bounded_iso8601(value: object) -> str:
    """Format only a real datetime; hostile objects never invoke user code."""
    if type(value) is not datetime:
        return "...[invalid-timestamp]"
    return _bounded_text(value.isoformat(), 64)


def _terminal_error_event(dto: StreamEvent | None) -> StreamEvent:
    return StreamEvent(
        name=EventKind.JOB_ERROR,
        id=str(getattr(dto, "id", "") or "error") if dto is not None else "error",
        data={
            "error": {
                "code": "STREAM_EVENT_TOO_LARGE",
                "message": "event could not be encoded within stream limits",
                "recoverable": True,
            }
        },
        terminal=True,
    )
