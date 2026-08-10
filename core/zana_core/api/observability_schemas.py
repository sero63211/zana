"""Local API schemas for bounded observability reads."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class SinkStatsRead(_Strict):
    events_written: int = Field(ge=0)
    events_dropped: int = Field(ge=0)
    bytes_written: int = Field(ge=0)
    failures: int = Field(ge=0)


class MemorySinkHealthRead(_Strict):
    present: bool
    max_events: int = Field(ge=0)
    max_bytes: int = Field(ge=0)
    retained_events: int = Field(ge=0)
    retained_bytes: int = Field(ge=0)
    stats: SinkStatsRead


class JsonlSinkHealthRead(_Strict):
    present: bool
    available: bool
    reason: str | None
    max_bytes: int | None
    max_retention: int | None
    filename: str | None
    log_root: str | None
    stats: SinkStatsRead


class ObservabilityHealthRead(_Strict):
    telemetry_enabled: bool
    remote_transport: str
    mode: str
    memory: MemorySinkHealthRead
    jsonl: JsonlSinkHealthRead
    total: SinkStatsRead
    max_retained_events: int
    retained_events: int
    retention_dropped: int


class ObservabilityEventRead(_Strict):
    sequence: int = Field(ge=0)
    event_id: str
    kind: str
    severity: str
    timestamp: str
    message: str
    context: dict[str, Any]
    payload: dict[str, Any]
    line_bytes: int = Field(ge=0)
    received_at: datetime
    invalid: bool = False


class ObservabilityEventPageRead(_Strict):
    items: list[ObservabilityEventRead]
    count: int = Field(ge=0)
    limit: int = Field(ge=1)
    next_cursor: int | None
    truncated: bool = False
    total_available: int = Field(ge=0)
    retention_dropped: int = Field(ge=0)
