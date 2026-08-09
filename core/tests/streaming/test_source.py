"""Bounded EventSource drain tests."""

from __future__ import annotations

from collections.abc import Iterator

from zana_core.streaming.encoder import SSEEncoder
from zana_core.streaming.models import (
    ErrorMetadata,
    EventBatch,
    EventCursor,
    EventKind,
    StreamEvent,
    StreamLimits,
)
from zana_core.streaming.source import drain


def _event(sequence: int, text: str = "x") -> StreamEvent:
    return StreamEvent(name=EventKind.TOKEN, data={"text": text}, id=f"s:{sequence}")


def _batch(cursor: EventCursor, events: tuple[StreamEvent, ...], **kwargs) -> EventBatch:
    return EventBatch(cursor=cursor, events=events, **kwargs)


class _SequenceSource:
    def __init__(self, batches: list[EventBatch]) -> None:
        self.batches = batches
        self.calls = 0

    def read_batch(self, cursor: EventCursor) -> EventBatch | None:
        self.calls += 1
        if self.calls > len(self.batches):
            return None
        return self.batches[self.calls - 1]


class _InfiniteSource:
    def __init__(self) -> None:
        self.cursor = EventCursor(sequence=0)
        self.calls = 0

    def read_batch(self, cursor: EventCursor) -> EventBatch:
        self.calls += 1
        batch = _batch(
            EventCursor(sequence=self.cursor.sequence),
            (_event(self.cursor.sequence),),
        )
        self.cursor = self.cursor.next()
        return batch


class _GeneratorSource:
    def __init__(self, generator: Iterator[EventBatch]) -> None:
        self.generator = generator

    def read_batch(self, cursor: EventCursor) -> EventBatch | None:
        return next(self.generator, None)


class _FailingSource:
    def read_batch(self, cursor: EventCursor) -> EventBatch:
        raise RuntimeError("secret failure detail")


class _CancelledSource:
    def read_batch(self, cursor: EventCursor) -> EventBatch:
        return _batch(cursor, (_event(cursor.sequence),))


def _collect_events(source, *, limits=None, **kwargs):
    emitted: list[StreamEvent] = []

    def on_event(item) -> None:
        emitted.append(item)

    result = drain(
        source,
        cursor=EventCursor(sequence=0),
        limits=limits,
        on_event=on_event,
        **kwargs,
    )
    return result, emitted


class TestDrain:
    def test_empty_source_ends_honestly(self) -> None:
        result, emitted = _collect_events(_SequenceSource([]))
        assert result.ended is True
        assert result.events_emitted == 0
        assert emitted == []

    def test_terminates_when_source_returns_no_events(self) -> None:
        source = _SequenceSource([_batch(EventCursor(sequence=0), ())])
        result, _ = _collect_events(source)
        assert result.ended is True

    def test_monotonic_batches_and_cursor(self) -> None:
        source = _SequenceSource(
            [
                _batch(EventCursor(sequence=0), (_event(0), _event(1))),
                _batch(EventCursor(sequence=2), (_event(2),)),
            ]
        )
        result, emitted = _collect_events(source)
        assert result.events_emitted == 3
        assert result.batches_read == 2
        assert result.final_cursor.sequence == 3
        assert [item.id for item in emitted] == ["s:0", "s:1", "s:2"]

    def test_infinite_generator_stopped_by_batch_cap(self) -> None:
        limits = StreamLimits(max_batches=3)
        result, emitted = _collect_events(_InfiniteSource(), limits=limits)
        assert result.events_emitted <= 3
        assert len(emitted) <= 3
        assert result.error is not None
        assert result.error.code == "STREAM_BATCH_LIMIT"

    def test_infinite_generator_stopped_by_event_cap(self) -> None:
        limits = StreamLimits(max_batch_events=2)
        result, _ = _collect_events(_InfiniteSource(), limits=limits)
        assert result.events_emitted <= 2

    def test_infinite_generator_stopped_by_total_bytes(self) -> None:
        limits = StreamLimits(max_total_bytes=100, max_batches=1000)
        result, emitted = _collect_events(_InfiniteSource(), limits=limits)
        assert len(emitted) >= 1
        assert result.bytes_emitted <= 100

    def test_cancellation(self) -> None:
        result, emitted = _collect_events(
            _CancelledSource(),
            cancelled=lambda: True,
        )
        assert result.cancelled is True
        assert result.events_emitted == 0
        assert emitted == []

    def test_deadline_with_injected_clock(self) -> None:
        clock = iter([0.0, 1.0, 5.0])
        result, _ = _collect_events(
            _InfiniteSource(),
            clock=lambda: next(clock),
            max_duration_seconds=2.0,
        )
        assert result.timed_out is True
        assert result.batches_read <= 2

    def test_source_error_is_sanitized(self) -> None:
        result, _ = _collect_events(_FailingSource())
        assert result.error is not None
        assert result.error.code == "SOURCE_ERROR"
        assert "secret failure detail" not in result.error.message

    def test_terminal_batch_ends(self) -> None:
        source = _SequenceSource([_batch(EventCursor(sequence=0), (_event(0),), terminal=True)])
        result, emitted = _collect_events(source)
        assert result.ended is True
        assert len(emitted) == 1

    def test_batch_error_metadata_ends(self) -> None:
        source = _SequenceSource(
            [
                _batch(
                    EventCursor(sequence=0),
                    (),
                    error=ErrorMetadata(code="JOB_FAILED", message="failed"),
                )
            ]
        )
        result, _ = _collect_events(source)
        assert result.ended is True
        assert result.error is not None
        assert result.error.code == "JOB_FAILED"


class TestOrdering:
    def test_stale_batch_cursor_is_rejected(self) -> None:
        source = _SequenceSource([_batch(EventCursor(sequence=0), (_event(0),))])
        result = drain(
            source,
            cursor=EventCursor(sequence=5),
            on_event=lambda _item: None,
        )
        assert result.error is not None
        assert result.error.code == "STREAM_CURSOR_STALE"

    def test_source_mismatch_is_rejected(self) -> None:
        source = _SequenceSource([_batch(EventCursor(source_id="other", sequence=0), (_event(0),))])
        result = drain(
            source,
            cursor=EventCursor(sequence=0),
            on_event=lambda _item: None,
        )
        assert result.error is not None
        assert result.error.code == "STREAM_SOURCE_MISMATCH"

    def test_cursor_gap_is_rejected(self) -> None:
        source = _SequenceSource([_batch(EventCursor(sequence=9), (_event(9),))])
        result = drain(
            source,
            cursor=EventCursor(sequence=0),
            on_event=lambda _item: None,
        )
        assert result.error is not None
        assert result.error.code == "STREAM_CURSOR_GAP"


class TestNoAccumulation:
    def test_encoder_retains_no_event_history(self) -> None:
        encoder = SSEEncoder()
        encoder.encode(_event(0))
        assert encoder.total_bytes > 0
        assert not hasattr(encoder, "history")

    def test_generator_source_not_collected_wholesale(self) -> None:
        def generate() -> Iterator[EventBatch]:
            yield _batch(EventCursor(sequence=0), (_event(0),))
            yield _batch(EventCursor(sequence=1), (_event(1),))

        generator = generate()
        source = _GeneratorSource(generator)
        result, emitted = _collect_events(source)
        assert result.events_emitted == 2
        assert len(emitted) == 2
