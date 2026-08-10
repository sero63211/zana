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


def test_close_is_idempotent_and_reads_remain_visible(tmp_path) -> None:  # noqa: ANN001
    jsonl = LocalJsonlSink(log_root=tmp_path, filename="events.jsonl", max_bytes=4096)
    registry = ObservabilityRegistry(
        memory_sink=BoundedMemorySink(max_events=10, max_bytes=100_000),
        jsonl_sink=jsonl,
    )
    registry.write(_event(1))
    registry.close()
    registry.close()
    health = registry.health()
    assert health.closed is True
    assert health.retained_events == 1
    assert health.retained_bytes > 0
    assert health.jsonl.available is False
    assert health.jsonl.reason == "CLOSED"
    assert registry.write(_event(2)).ok is False
    assert registry.events().count == 1
    assert registry.events().retained_bytes > 0


def test_byte_cap_evicts_oldest_and_tracks_drop_bytes() -> None:
    registry = ObservabilityRegistry(
        memory_sink=_memory_sink(),
        max_retained_bytes=200,
    )
    for index in range(1, 6):
        registry.write(_event(index))
    health = registry.health()
    assert health.retained_bytes <= 200
    assert health.retention_dropped > 0
    assert health.retention_dropped_bytes > 0
    page = registry.events()
    assert page.retained_bytes <= 200
    assert page.retention_dropped_bytes == health.retention_dropped_bytes


def test_partial_delivery_is_explicit_and_noncrashing(tmp_path) -> None:  # noqa: ANN001
    from zana_core.observability.sinks import LocalJsonlSink, WriteResult

    jsonl = LocalJsonlSink(
        log_root=tmp_path,
        filename="events-partial.jsonl",
        max_bytes=4096,
    )
    registry = ObservabilityRegistry(memory_sink=_memory_sink(), jsonl_sink=jsonl)
    original = registry._memory.write
    registry._memory.write = lambda event: WriteResult(ok=False, event_id="", error="WRITE_FAILED")  # type: ignore[method-assign]
    result = registry.write(_event(1))
    assert result.ok is True
    assert result.error == "PARTIAL_DELIVERY"
    assert registry.health().partial_deliveries == 1
    assert registry.health().retained_events == 1
    registry._memory.write = original


def test_write_rejects_non_event() -> None:
    registry = ObservabilityRegistry(memory_sink=_memory_sink())
    result = registry.write("not-an-event")  # type: ignore[arg-type]
    assert result.ok is False
    assert result.error == "WRITE_REJECTED"
    assert registry.health().failures == 1


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


def test_safe_public_identifier_redacts_private_and_sensitive_values() -> None:
    from zana_core.observability.registry import safe_public_identifier

    safe = safe_public_identifier("op-123")
    assert safe == "op-123"
    assert safe_public_identifier("") == ""
    for unsafe in (
        "/private/tmp/op",
        "C:\\Users\\x",
        "token-abc",
        "BearerToken",
        "authorization_foo",
        "credentials",
        "secret-value",
        "x" * 129,
    ):
        redacted = safe_public_identifier(unsafe)
        assert redacted.startswith("redacted-")
        assert unsafe not in redacted
        assert "/" not in redacted
        assert "\\" not in redacted
        assert "token" not in redacted.lower()


def test_sinks_receive_one_sanitized_event(tmp_path) -> None:  # noqa: ANN001
    from zana_core.observability.registry import safe_public_identifier
    from zana_core.observability.sinks import LocalJsonlSink

    jsonl = LocalJsonlSink(log_root=tmp_path, filename="events.jsonl", max_bytes=4096)
    memory = _memory_sink()
    registry = ObservabilityRegistry(memory_sink=memory, jsonl_sink=jsonl)
    received: list[Event] = []
    original = memory.write
    memory.write = lambda event: (received.append(event), original(event))[1]  # type: ignore[method-assign]
    registry.write(
        Event(
            kind=EventKind.SYSTEM,
            severity=Severity.INFO,
            message="hostile ids",
            operation_id="BearerToken-abc",
            job_id="/private/tmp/job",
            phase="token-phase",
            recovery_code="credentials",
            context={
                "operation_id": "authorization_foo",
                "job_id": "C:\\Users\\x",
                "phase": "secret-phase",
                "instance_id": "token-instance",
                "image_digest": "token-digest",
            },
        )
    )
    assert len(received) == 1
    safe = received[0]
    assert safe.operation_id == safe_public_identifier("BearerToken-abc")
    assert safe.job_id == safe_public_identifier("/private/tmp/job")
    assert safe.phase == safe_public_identifier("token-phase")
    assert safe.recovery_code == safe_public_identifier("credentials")
    assert safe.context.operation_id == safe_public_identifier("authorization_foo")
    assert safe.context.job_id == safe_public_identifier("C:\\Users\\x")
    assert safe.context.phase == safe_public_identifier("secret-phase")
    assert safe.context.instance_id == safe_public_identifier("token-instance")
    assert safe.context.image_digest == safe_public_identifier("token-digest")
    memory.write = original

    memory_lines = "".join(record.line for record in memory.snapshot())
    disk_lines = (tmp_path / "events.jsonl").read_text()
    for raw in (
        "BearerToken-abc",
        "/private/tmp/job",
        "token-phase",
        "credentials",
        "authorization_foo",
        "C:\\Users\\x",
        "secret-phase",
        "token-instance",
        "token-digest",
    ):
        assert raw not in memory_lines
        assert raw not in disk_lines


def test_failure_counters_cover_rejections() -> None:
    registry = ObservabilityRegistry(memory_sink=_memory_sink())
    assert registry.write("not-an-event").ok is False  # type: ignore[arg-type]
    assert registry.health().failures == 1

    disabled = ObservabilityRegistry()
    result = disabled.write(_event(1))
    assert result.ok is False
    assert result.error == "NO_SINKS_CONFIGURED"
    assert disabled.health().failures == 1

    import zana_core.observability.registry as registry_module

    original = registry_module.serialize_event

    def boom(event):  # noqa: ANN001, ANN202
        raise RuntimeError("injected serialization failure")

    registry_module.serialize_event = boom
    try:
        result = registry.write(_event(2))
        assert result.ok is False
        assert result.error == "WRITE_FAILED"
        assert registry.health().failures == 2
    finally:
        registry_module.serialize_event = original
