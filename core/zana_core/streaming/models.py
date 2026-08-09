"""Strict frozen models for bounded stream events and cursors."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EventKind(str, Enum):
    """Canonical stream event kinds without importing job/chat modules.

    Covers generic job progress and the chat stream event names from the
    backend API contract.
    """

    JOB_CREATED = "job_created"
    JOB_PROGRESS = "job_progress"
    JOB_STATUS = "job_status"
    JOB_ERROR = "job_error"
    JOB_CANCELLED = "job_cancelled"
    MESSAGE_START = "message_start"
    RETRIEVAL = "retrieval"
    TOOL_REQUEST = "tool_request"
    TOOL_RESULT = "tool_result"
    TOKEN = "token"
    MESSAGE_END = "message_end"
    ERROR = "error"
    KEEPALIVE = "keepalive"


class StreamLimits(BaseModel):
    """Bounded caps for encoding, batches, cursors, and redaction."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_data_bytes: int = Field(default=64 * 1024, gt=0)
    max_event_bytes: int = Field(default=96 * 1024, gt=0)
    max_batch_events: int = Field(default=256, gt=0)
    max_identifier_chars: int = Field(default=128, gt=0)
    max_name_chars: int = Field(default=64, gt=0)
    max_recovery_chars: int = Field(default=300, gt=0)
    max_cursor_chars: int = Field(default=512, gt=0)
    max_retry_ms: int = Field(default=30_000, gt=0)
    max_total_bytes: int = Field(default=4 * 1024 * 1024, gt=0)
    max_batches: int = Field(default=1024, gt=0)
    max_duration_seconds: float = Field(default=60.0, gt=0.0)


class InvalidCursorError(ValueError):
    """Raised when a cursor header cannot be parsed."""


class CursorStatus(str, Enum):
    """Explicit cursor resume outcome."""

    VALID = "valid"
    STALE = "stale"
    INVALID = "invalid"
    AHEAD = "ahead"


class EventCursor(BaseModel):
    """Typed monotonic numeric stream cursor compatible with Last-Event-ID."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(default="default", min_length=1, max_length=128)
    sequence: int = Field(default=0, ge=0)

    @field_validator("source_id")
    @classmethod
    def validate_source_id(cls, value: str) -> str:
        if any(character in value for character in ":\r\n\x00"):
            raise ValueError("source_id must not contain ':', CR, LF, or NUL.")
        return value

    def to_header(self) -> str:
        """Serialize as ``<source>:<sequence>`` for Last-Event-ID."""
        return f"{self.source_id}:{self.sequence}"

    def next(self, sequence: int | None = None) -> EventCursor:
        """Return the cursor after consuming ``sequence`` (default +1)."""
        target = sequence if sequence is not None else self.sequence + 1
        if target < self.sequence:
            raise InvalidCursorError("cursor cannot move backwards")
        return EventCursor(source_id=self.source_id, sequence=target)

    @classmethod
    def parse(cls, value: str, *, default_source: str = "default") -> EventCursor:
        """Parse a Last-Event-ID header into a typed cursor."""
        if not isinstance(value, str) or not value:
            raise InvalidCursorError("empty cursor header")
        if any(character in value for character in "\r\n\x00"):
            raise InvalidCursorError("cursor header contains control characters")
        if ":" in value:
            source, _, sequence_text = value.partition(":")
            if not source or ":" in sequence_text:
                raise InvalidCursorError("cursor header must be '<source>:<sequence>'")
        else:
            source = default_source
            sequence_text = value
        try:
            sequence = int(sequence_text)
        except ValueError as error:
            raise InvalidCursorError("cursor sequence must be an integer") from error
        if sequence < 0:
            raise InvalidCursorError("cursor sequence must be non-negative")
        return cls(source_id=source, sequence=sequence)


class ErrorMetadata(BaseModel):
    """Terminal/error metadata; never carries raw exception or secrets."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=500)
    recoverable: bool = True
    recovery_action: str = Field(default="", max_length=300)
    terminal: bool = True


class StreamEvent(BaseModel):
    """One canonical stream event."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: EventKind
    data: Any = None
    id: str | None = Field(default=None, max_length=128)
    retry_ms: int | None = Field(default=None, ge=0)
    terminal: bool = False
    error: ErrorMetadata | None = None

    @field_validator("id")
    @classmethod
    def validate_event_id(cls, value: str | None) -> str | None:
        if value is not None and any(character in value for character in "\r\n\x00"):
            raise ValueError("event id must not contain CR, LF, or NUL")
        return value


class EventBatch(BaseModel):
    """One bounded batch read after a cursor."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cursor: EventCursor
    events: tuple[StreamEvent, ...] = Field(default_factory=tuple)
    terminal: bool = False
    error: ErrorMetadata | None = None


class CursorCheck(BaseModel):
    """Outcome of validating a resume cursor against a known position."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: CursorStatus
    reason: str
    cursor: EventCursor
    expected_sequence: int


class ResumeDecision(BaseModel):
    """High-level resume result for API callers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    accepted: bool
    status: CursorStatus
    reason: str
    cursor: EventCursor


def check_cursor(
    cursor: EventCursor,
    expected_sequence: int,
    *,
    allow_ahead: bool = True,
) -> CursorCheck:
    """Compare a typed cursor to the current known sequence."""
    if cursor.sequence < expected_sequence:
        return CursorCheck(
            status=CursorStatus.STALE,
            reason="cursor sequence is older than the current stream position",
            cursor=cursor,
            expected_sequence=expected_sequence,
        )
    if cursor.sequence > expected_sequence:
        if allow_ahead:
            return CursorCheck(
                status=CursorStatus.AHEAD,
                reason="cursor is ahead; no fake replay is guaranteed",
                cursor=cursor,
                expected_sequence=expected_sequence,
            )
        return CursorCheck(
            status=CursorStatus.INVALID,
            reason="cursor is ahead of the stream and is not accepted",
            cursor=cursor,
            expected_sequence=expected_sequence,
        )
    return CursorCheck(
        status=CursorStatus.VALID,
        reason="cursor matches the current stream position",
        cursor=cursor,
        expected_sequence=expected_sequence,
    )


def resume_decision(
    cursor: EventCursor,
    expected_sequence: int,
    *,
    allow_ahead: bool = True,
) -> ResumeDecision:
    """Produce the API-facing resume outcome."""
    check = check_cursor(cursor, expected_sequence, allow_ahead=allow_ahead)
    return ResumeDecision(
        accepted=check.status in {CursorStatus.VALID, CursorStatus.AHEAD},
        status=check.status,
        reason=check.reason,
        cursor=cursor,
    )
