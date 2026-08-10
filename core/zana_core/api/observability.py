"""Authenticated local observability event and sink health router."""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from zana_core.api.deps import verify_token
from zana_core.api.errors import http_error
from zana_core.api.observability_schemas import (
    JsonlSinkHealthRead,
    MemorySinkHealthRead,
    ObservabilityEventPageRead,
    ObservabilityEventRead,
    ObservabilityHealthRead,
    SinkStatsRead,
)
from zana_core.observability.registry import (
    MAX_EVENT_PAGE_LIMIT,
    EventPage,
    JsonlSinkHealth,
    MemorySinkHealth,
    ObservabilityHealth,
    ObservabilityRegistry,
    RetainedEvent,
)
from zana_core.observability.sinks import SinkStats
from zana_core.streaming.redaction import RedactionLimits, Redactor

router = APIRouter(
    prefix="/api/v1/observability",
    tags=["observability"],
    dependencies=[Depends(verify_token)],
)

_EVENT_LIMITS = RedactionLimits(
    max_items=256,
    max_container_items=128,
    max_depth=12,
    max_string_length=512,
    max_string_bytes=1024,
    max_key_length=128,
    max_key_bytes=256,
    max_output_bytes=8192,
)


def _registry(request: Request) -> ObservabilityRegistry:
    registry = getattr(request.app.state, "observability_registry", None)
    if type(registry) is not ObservabilityRegistry:
        raise http_error(
            503,
            "OBSERVABILITY_SERVICE_UNAVAILABLE",
            "The local observability registry is not configured on this Core app.",
            recoverable=True,
            actions=["register_observability_routes"],
        )
    return registry


@router.get("/events", response_model=ObservabilityEventPageRead)
def observability_events(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=MAX_EVENT_PAGE_LIMIT)] = 50,
    before_sequence: Annotated[int | None, Query(ge=0)] = None,
) -> ObservabilityEventPageRead:
    """Return recent local redacted events, newest first, with a bounded cursor."""
    try:
        page = _registry(request).events(limit=limit, before_sequence=before_sequence)
    except ValueError:
        raise http_error(
            400,
            "INVALID_EVENT_CURSOR",
            "Event pagination bounds are invalid.",
            recoverable=True,
            actions=["fix_page_bounds"],
        ) from None
    return _event_page_read(page)


@router.get("/health", response_model=ObservabilityHealthRead)
def observability_health(request: Request) -> ObservabilityHealthRead:
    """Return bounded local sink health; telemetry is always explicitly off."""
    return _health_read(_registry(request).health())


def _event_page_read(page: EventPage) -> ObservabilityEventPageRead:
    return ObservabilityEventPageRead(
        items=[_event_read(record) for record in page.items],
        count=page.count,
        limit=page.limit,
        next_cursor=page.next_cursor,
        truncated=page.truncated,
        total_available=page.total_available,
        retention_dropped=page.retention_dropped,
    )


def _event_read(record: RetainedEvent) -> ObservabilityEventRead:
    parsed: dict[str, Any] = {}
    invalid = False
    try:
        loaded = json.loads(record.line)
        if type(loaded) is not dict:
            raise ValueError("event line is not an object")
        redacted = Redactor(_EVENT_LIMITS).redact(loaded)
        if type(redacted) is not dict:
            invalid = True
        else:
            parsed = redacted
    except Exception:
        invalid = True
    return ObservabilityEventRead(
        sequence=record.sequence,
        event_id=record.event_id,
        kind=_text(parsed.get("kind"), "unknown", 64),
        severity=_text(parsed.get("severity"), "unknown", 64),
        timestamp=_text(parsed.get("timestamp"), "", 64),
        message=_text(parsed.get("message"), "", 512),
        context=_mapping(parsed.get("context")),
        payload=_mapping(parsed.get("payload")),
        line_bytes=record.bytes,
        received_at=record.received_at,
        invalid=invalid,
    )


def _text(value: object, default: str, max_length: int) -> str:
    if type(value) is not str or len(value) > max_length:
        return default
    return value


def _mapping(value: object) -> dict[str, Any]:
    return value if type(value) is dict else {}


def _health_read(health: ObservabilityHealth) -> ObservabilityHealthRead:
    return ObservabilityHealthRead(
        telemetry_enabled=health.telemetry_enabled,
        remote_transport=health.remote_transport,
        mode=health.mode,
        memory=_memory_read(health.memory),
        jsonl=_jsonl_read(health.jsonl),
        total=_stats_read(health.total),
        max_retained_events=health.max_retained_events,
        retained_events=health.retained_events,
        retention_dropped=health.retention_dropped,
    )


def _memory_read(health: MemorySinkHealth) -> MemorySinkHealthRead:
    return MemorySinkHealthRead(
        present=health.present,
        max_events=health.max_events,
        max_bytes=health.max_bytes,
        retained_events=health.retained_events,
        retained_bytes=health.retained_bytes,
        stats=_stats_read(health.stats),
    )


def _jsonl_read(health: JsonlSinkHealth) -> JsonlSinkHealthRead:
    return JsonlSinkHealthRead(
        present=health.present,
        available=health.available,
        reason=health.reason,
        max_bytes=health.max_bytes,
        max_retention=health.max_retention,
        filename=health.filename,
        log_root=health.log_root,
        stats=_stats_read(health.stats),
    )


def _stats_read(stats: SinkStats) -> SinkStatsRead:
    return SinkStatsRead(
        events_written=stats.events_written,
        events_dropped=stats.events_dropped,
        bytes_written=stats.bytes_written,
        failures=stats.failures,
    )
