"""Bounded low-resource limits for native acquisition streaming."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_LINE_BYTES = 8 * 1024
MAX_TOTAL_EVENT_BYTES = 64 * 1024
MAX_EVENT_COUNT = 2_000
MAX_RETAINED_EVENTS = 50
MAX_MODEL_REFERENCE_BYTES = 200
MAX_CONCURRENT_ACQUISITIONS = 8
MAX_DEADLINE_SECONDS = 3600
MAX_PROGRESS_VALUE = 1 << 40
MAX_SEQUENCE = MAX_EVENT_COUNT
MAX_ERROR_CODE_LENGTH = 64
MAX_ADMISSION_HEADROOM = 1 << 40


class AcquisitionLimits(BaseModel):
    """Hard caps preventing unbounded buffers or histories."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_line_bytes: int = Field(default=MAX_LINE_BYTES, gt=0, le=MAX_LINE_BYTES)
    max_total_event_bytes: int = Field(
        default=MAX_TOTAL_EVENT_BYTES, gt=0, le=MAX_TOTAL_EVENT_BYTES
    )
    max_event_count: int = Field(default=MAX_EVENT_COUNT, gt=0, le=MAX_EVENT_COUNT)
    max_retained_events: int = Field(default=MAX_RETAINED_EVENTS, gt=0, le=MAX_RETAINED_EVENTS)
    max_model_reference_bytes: int = Field(
        default=MAX_MODEL_REFERENCE_BYTES, gt=0, le=MAX_MODEL_REFERENCE_BYTES
    )
    max_concurrent_acquisitions: int = Field(default=1, ge=1, le=MAX_CONCURRENT_ACQUISITIONS)

    @model_validator(mode="after")
    def _validate_consistency(self) -> AcquisitionLimits:
        if self.max_retained_events > self.max_event_count:
            raise ValueError("max_retained_events must not exceed max_event_count")
        if self.max_line_bytes > self.max_total_event_bytes:
            raise ValueError("max_line_bytes must not exceed max_total_event_bytes")
        return self
