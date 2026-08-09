"""Stream model, cursor, and resume contract tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from zana_core.streaming.models import (
    CursorStatus,
    ErrorMetadata,
    EventBatch,
    EventCursor,
    EventKind,
    InvalidCursorError,
    ResumeDecision,
    StreamEvent,
    StreamLimits,
    check_cursor,
    resume_decision,
)


class TestEventKinds:
    def test_generic_job_and_chat_event_names(self) -> None:
        assert EventKind.JOB_PROGRESS.value == "job_progress"
        assert EventKind.MESSAGE_START.value == "message_start"
        assert EventKind.TOKEN.value == "token"
        assert EventKind.MESSAGE_END.value == "message_end"
        assert EventKind.ERROR.value == "error"


class TestStrictModels:
    def test_stream_limits_reject_unknown_fields(self) -> None:
        with pytest.raises(ValidationError):
            StreamLimits(unknown=True)

    def test_event_rejects_unknown_fields(self) -> None:
        with pytest.raises(ValidationError):
            StreamEvent(name=EventKind.TOKEN, unexpected=1)

    def test_event_rejects_control_chars_in_id(self) -> None:
        with pytest.raises(ValidationError):
            StreamEvent(name=EventKind.TOKEN, id="bad\nid")

    def test_error_metadata_is_terminal(self) -> None:
        error = ErrorMetadata(
            code="MODEL_NOT_AVAILABLE",
            message="required base model is not available",
            recovery_action="refresh_models",
        )
        assert error.terminal is True

    def test_batch_holds_events_and_cursor(self) -> None:
        batch = EventBatch(
            cursor=EventCursor(source_id="jobs", sequence=5),
            events=(StreamEvent(name=EventKind.JOB_PROGRESS, data={"p": 0.5}),),
            terminal=False,
        )
        assert batch.events[0].data == {"p": 0.5}


class TestCursor:
    def test_to_header_and_parse_round_trip(self) -> None:
        cursor = EventCursor(source_id="jobs", sequence=12)
        assert EventCursor.parse(cursor.to_header()) == cursor

    def test_parse_default_source_and_sequence(self) -> None:
        assert EventCursor.parse("7") == EventCursor(source_id="default", sequence=7)

    def test_invalid_cursor_values(self) -> None:
        for value in ["", "a:b:c", "-1", "x\n", "jobs:abc"]:
            with pytest.raises(InvalidCursorError):
                EventCursor.parse(value)

    def test_next_is_monotonic(self) -> None:
        cursor = EventCursor(source_id="jobs", sequence=3)
        assert cursor.next().sequence == 4
        with pytest.raises(InvalidCursorError):
            cursor.next(sequence=2)

    def test_check_cursor_stale_valid_ahead(self) -> None:
        assert check_cursor(EventCursor(sequence=1), 5).status is CursorStatus.STALE
        assert check_cursor(EventCursor(sequence=5), 5).status is CursorStatus.VALID
        assert check_cursor(EventCursor(sequence=9), 5).status is CursorStatus.AHEAD

    def test_resume_decision_explicit_outcomes(self) -> None:
        decision: ResumeDecision = resume_decision(EventCursor(sequence=1), 5)
        assert decision.accepted is False
        assert decision.status is CursorStatus.STALE
        ahead = resume_decision(EventCursor(sequence=9), 5, allow_ahead=False)
        assert ahead.accepted is False
        assert ahead.status is CursorStatus.INVALID
