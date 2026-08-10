"""Focused tests for the bounded local observability registry."""

from __future__ import annotations

import pytest

from zana_core.observability.events import Event, EventKind, Severity
from zana_core.observability.registry import (
    MAX_EVENT_PAGE_LIMIT,
    MAX_RETAINED_EVENTS_HARD_CAP,
    ObservabilityRegistry,
)
from zana_core.observability.sinks import BoundedMemorySink, LocalJsonlSink


def _event(index: int, **payload: object) -> Event:
    return Event(
        kind=EventKind.SYSTEM,
        severity=Severity.INFO,
        message=f"event {index}",
        operation_id=f"op-{index}",
        payload=payload,
    )


def _memory_sink() -> BoundedMemorySink:
    return BoundedMemorySink(max_events=100, max_bytes=1_000_000)


def test_write_retains_bounded_redacted_records() -> None:
    registry = ObservabilityRegistry(memory_sink=_memory_sink())
    for index in range(1, 6):
        result = registry.write(_event(index))
        assert result.ok is True
        assert result.event_id == f"op-{index}"
    health = registry.health()
    assert health.retained_events == 5
    assert health.memory.present is True
    page = registry.events(limit=3)
    assert page.count == 3
    assert page.truncated is True
    assert page.next_cursor == 3
    assert [record.sequence for record in page.items] == [5, 4, 3]


def test_retention_trims_oldest_and_is_explicit() -> None:
    registry = ObservabilityRegistry(memory_sink=_memory_sink(), max_retained_events=3)
    for index in range(1, 6):
        assert registry.write(_event(index)).ok is True
    health = registry.health()
    assert health.retention_dropped == 2
    assert health.retained_events == 3
    page = registry.events()
    assert page.total_available == 3
    assert [record.sequence for record in page.items] == [5, 4, 3]


def test_events_before_cursor_walks_backward() -> None:
    registry = ObservabilityRegistry(memory_sink=_memory_sink())
    for index in range(1, 6):
        registry.write(_event(index))
    first = registry.events(before_sequence=5, limit=2)
    assert [record.sequence for record in first.items] == [4, 3]
    assert first.next_cursor == 3
    second = registry.events(before_sequence=first.next_cursor, limit=2)
    assert [record.sequence for record in second.items] == [2, 1]
    assert second.next_cursor is None
    assert second.truncated is False


def test_health_reports_telemetry_off_and_sink_stats(tmp_path) -> None:  # noqa: ANN001
    jsonl = LocalJsonlSink(log_root=tmp_path, filename="events.jsonl", max_bytes=4096)
    registry = ObservabilityRegistry(memory_sink=_memory_sink(), jsonl_sink=jsonl)
    assert registry.write(_event(1)).ok is True
    health = registry.health()
    assert health.telemetry_enabled is False
    assert health.remote_transport == "none"
    assert health.mode == "local_memory_jsonl"
    assert health.memory.present is True
    assert health.memory.stats.events_written == 1
    assert health.jsonl.available is True
    assert health.jsonl.stats.events_written == 1
    assert health.total.events_written == 2
    assert health.jsonl.log_root != str(tmp_path)
    assert str(tmp_path) not in health.jsonl.log_root


def test_jsonl_unsupported_state_is_explicit() -> None:
    registry = ObservabilityRegistry(
        memory_sink=_memory_sink(),
        jsonl_error="PLATFORM_UNSUPPORTED",
    )
    health = registry.health()
    assert health.mode == "local_memory"
    assert health.jsonl.present is False
    assert health.jsonl.available is False
    assert health.jsonl.reason == "PLATFORM_UNSUPPORTED"
    assert health.jsonl.log_root is None


def test_disabled_registry_write_fails_closed() -> None:
    registry = ObservabilityRegistry()
    result = registry.write(_event(1))
    assert result.ok is False
    assert result.error == "NO_SINKS_CONFIGURED"
    health = registry.health()
    assert health.mode == "disabled"
    assert health.telemetry_enabled is False


def test_write_rejects_non_event() -> None:
    registry = ObservabilityRegistry(memory_sink=_memory_sink())
    result = registry.write("not-an-event")  # type: ignore[arg-type]
    assert result.ok is False
    assert result.error == "WRITE_REJECTED"


def test_event_payload_is_redacted_before_retention() -> None:
    registry = ObservabilityRegistry(memory_sink=_memory_sink())
    registry.write(
        _event(
            1,
            password="super-secret-value",
            path="/private/tmp/secret.txt",
            public="visible",
        )
    )
    page = registry.events()
    line = page.items[0].line
    assert "super-secret-value" not in line
    assert "/private/tmp/secret.txt" not in line
    assert "public" in line
    assert "visible" in line


def test_constructor_rejects_bad_bounds_and_reasons(tmp_path) -> None:  # noqa: ANN001
    with pytest.raises(ValueError):
        ObservabilityRegistry(memory_sink=_memory_sink(), max_retained_events=0)
    with pytest.raises(ValueError):
        ObservabilityRegistry(max_retained_events=MAX_RETAINED_EVENTS_HARD_CAP + 1)
    with pytest.raises(ValueError):
        ObservabilityRegistry(memory_sink=_memory_sink(), jsonl_error="UNKNOWN_REASON")
    with pytest.raises(ValueError):
        ObservabilityRegistry(
            memory_sink=_memory_sink(),
            jsonl_error="PLATFORM_UNSUPPORTED",
            jsonl_sink=LocalJsonlSink(log_root=tmp_path, filename="x.jsonl"),
        )


def test_page_bounds_are_strict() -> None:
    registry = ObservabilityRegistry(memory_sink=_memory_sink())
    with pytest.raises(ValueError):
        registry.events(limit=0)
    with pytest.raises(ValueError):
        registry.events(limit=MAX_EVENT_PAGE_LIMIT + 1)
    with pytest.raises(ValueError):
        registry.events(before_sequence=-1)
