"""Deterministic JSON Lines serialization and size bound tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from zana_core.observability.events import (
    MAX_DURATION_MS,
    MAX_SCHEMA_VERSION,
    Event,
    EventContext,
    EventKind,
    Severity,
    _FrozenDict,
)
from zana_core.observability.serialization import (
    MAX_ENCODED_LINE_BYTES,
    JsonLinesCodec,
    serialize_event,
)


def _event(payload: dict | None = None, **overrides) -> Event:
    defaults = {
        "kind": EventKind.SYSTEM,
        "severity": Severity.INFO,
        "message": "hello",
        "context": EventContext(operation_id="op-1", job_id="job-1", phase="phase-1"),
        "payload": payload or {},
    }
    defaults.update(overrides)
    return Event(**defaults)


class TestSerialization:
    def test_deterministic_canonical_lines(self) -> None:
        timestamp = datetime(2026, 1, 1, tzinfo=UTC)
        first = serialize_event(_event({"a": 1, "b": 2}, timestamp=timestamp))
        second = serialize_event(_event({"b": 2, "a": 1}, timestamp=timestamp))
        assert first == second
        parsed = json.loads(first)
        assert parsed["schema_version"] == 1
        assert parsed["kind"] == "system"

    def test_secret_payload_redacted_before_serialization(self) -> None:
        line = serialize_event(_event({"token": "super-secret", "ok": True}))
        assert "super-secret" not in line
        assert "***" in line

    def test_huge_string_rejected_before_materialization(self) -> None:
        with pytest.raises(ValidationError):
            _event({"data": "x" * 100000})
        line = serialize_event(_event({"data": "x" * 500}))
        assert json.loads(line)["payload"]["data"] == "x" * 500

    def test_single_newline_contract(self) -> None:
        encoded = JsonLinesCodec().encode(_event({"a": 1}))
        assert encoded.endswith("\n")
        assert not encoded.endswith("\n\n")
        assert encoded.count("\n") == 1

    def test_exact_event_required(self) -> None:
        with pytest.raises(TypeError):
            serialize_event(object())
        with pytest.raises(TypeError):
            serialize_event({"kind": "system"})

    def test_encoded_bytes_are_exact(self) -> None:
        event = _event({"a": 1})
        codec = JsonLinesCodec()
        assert codec.encoded_bytes(event) == len(serialize_event(event).encode("utf-8"))

    def test_allow_nan_false_never_emits_nonfinite(self) -> None:
        line = serialize_event(_event({"ok": 1.5}))
        assert "NaN" not in line
        assert "Infinity" not in line


class TestOversizeAndFailureFallback:
    def test_oversize_event_dropped_to_small_record(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "zana_core.observability.serialization.MAX_ENCODED_LINE_BYTES",
            64,
        )
        line = serialize_event(_event({f"key_{index}": "x" * 80 for index in range(10)}))
        assert len(line.encode("utf-8")) < MAX_ENCODED_LINE_BYTES
        parsed = json.loads(line)
        assert parsed["recovery_code"] == "EVENT_OVERSIZE"

    def test_fallback_is_fixed_safe_fields_only(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "zana_core.observability.serialization.MAX_ENCODED_LINE_BYTES",
            64,
        )
        line = serialize_event(
            _event(
                {"secret": "super-secret", "path": "/private/secret-doc.md"},
                operation_id="op-caller",
                job_id="job-caller",
            )
        )
        parsed = json.loads(line)
        assert parsed == {
            "schema_version": 1,
            "kind": "system",
            "severity": "warning",
            "message": "event dropped: serialization bound exceeded",
            "operation_id": "",
            "job_id": "",
            "phase": "",
            "recovery_code": "EVENT_OVERSIZE",
        }
        assert "super-secret" not in line
        assert "secret-doc.md" not in line

    def test_redaction_failure_returns_fixed_fallback(self, monkeypatch) -> None:
        def fail(_event):
            raise ValueError("raw detail")

        monkeypatch.setattr("zana_core.observability.serialization.redact_event", fail)
        line = serialize_event(_event({"token": "secret"}))
        parsed = json.loads(line)
        assert parsed["recovery_code"] == "EVENT_OVERSIZE"
        assert "secret" not in line

    def test_hostile_payload_returns_fixed_fallback(self) -> None:
        event = _event({"ok": True})
        object.__setattr__(event, "payload", {"token": "super-secret"})
        line = serialize_event(event)
        assert "super-secret" not in line
        assert json.loads(line)["recovery_code"] == "EVENT_OVERSIZE"


class TestSafeOperationalIds:
    def test_control_characters_rejected(self) -> None:
        for bad in ("bad\nid", "bad\rid", "bad\x00id", "bad\x1fid"):
            with pytest.raises(ValidationError):
                _event(operation_id=bad)
        with pytest.raises(ValidationError):
            _event(phase="bad\nphase")

    def test_safe_ids_serialized(self) -> None:
        line = serialize_event(_event(operation_id="op-1", job_id="job-1", phase="phase-1"))
        parsed = json.loads(line)
        assert parsed["operation_id"] == "op-1"
        assert parsed["job_id"] == "job-1"
        assert parsed["phase"] == "phase-1"

    def test_malicious_constructed_id_falls_back(self) -> None:
        event = Event.model_construct(
            kind=EventKind.SYSTEM,
            severity=Severity.INFO,
            message="x",
            operation_id="bad\nid",
        )
        line = serialize_event(event)
        parsed = json.loads(line)
        assert parsed["operation_id"] == ""
        assert parsed["recovery_code"] == "EVENT_OVERSIZE"
        assert "bad" not in line


class TestCorruptedExactEventSerialization:
    def test_hostile_tzinfo_emits_only_dropped_record(self) -> None:
        from datetime import tzinfo

        class EvilTZ(tzinfo):
            def __init__(self) -> None:
                self.hooks: list[str] = []

            def utcoffset(self, dt):
                self.hooks.append("utcoffset")
                raise AssertionError("hostile utcoffset must not run")

            def dst(self, dt):
                self.hooks.append("dst")
                raise AssertionError("hostile dst must not run")

            def tzname(self, dt):
                self.hooks.append("tzname")
                return "secret"

            def __repr__(self):
                self.hooks.append("repr")
                return "secret"

        evil = EvilTZ()
        event = _event(payload={"token": "super-secret"})
        object.__setattr__(event, "timestamp", datetime(2026, 1, 1, tzinfo=evil))
        line = serialize_event(event)
        assert json.loads(line)["recovery_code"] == "EVENT_OVERSIZE"
        assert "super-secret" not in line
        assert evil.hooks == []

    def test_model_construct_invalid_ranges_emit_only_dropped_record(self) -> None:
        cases = [
            {"schema_version": 0},
            {"schema_version": MAX_SCHEMA_VERSION + 1},
            {"progress_0_1": 1.5},
            {"duration_ms": -1},
            {"duration_ms": MAX_DURATION_MS + 1},
        ]
        for overrides in cases:
            event = Event.model_construct(
                kind=EventKind.SYSTEM,
                severity=Severity.INFO,
                payload={"token": "super-secret"},
                **overrides,
            )
            line = serialize_event(event)
            parsed = json.loads(line)
            assert parsed == {
                "schema_version": 1,
                "kind": "system",
                "severity": "warning",
                "message": "event dropped: serialization bound exceeded",
                "operation_id": "",
                "job_id": "",
                "phase": "",
                "recovery_code": "EVENT_OVERSIZE",
            }
            assert "super-secret" not in line

    def test_model_construct_utf8_message_emits_dropped_record(self) -> None:
        event = Event.model_construct(
            kind=EventKind.SYSTEM,
            severity=Severity.INFO,
            message="é" * 513,
            payload={"token": "super-secret"},
        )
        line = serialize_event(event)
        assert json.loads(line)["recovery_code"] == "EVENT_OVERSIZE"
        assert "super-secret" not in line

    def test_object_setattr_corruption_emits_only_dropped_record(self) -> None:
        event = _event(payload={"token": "super-secret"})
        object.__setattr__(event, "schema_version", 0)
        line = serialize_event(event)
        assert json.loads(line)["recovery_code"] == "EVENT_OVERSIZE"
        assert "super-secret" not in line
        assert '"schema_version":0' not in line

    def test_corrupted_context_emits_only_dropped_record(self) -> None:
        context = EventContext.model_construct(operation_id="bad\nid")
        event = Event.model_construct(
            kind=EventKind.SYSTEM,
            severity=Severity.INFO,
            message="x",
            context=context,
        )
        line = serialize_event(event)
        assert json.loads(line)["recovery_code"] == "EVENT_OVERSIZE"
        assert "bad" not in line

    def test_base_constructed_frozen_payload_emits_only_dropped_record(self) -> None:
        invalid = tuple.__new__(_FrozenDict, (("bad", object()),))
        with pytest.raises(ValueError):
            Event.model_construct(
                kind=EventKind.SYSTEM,
                severity=Severity.INFO,
                payload=invalid,
            )

    def test_hostile_field_hooks_never_invoked(self) -> None:
        class Evil:
            def __init__(self) -> None:
                self.hooks: list[str] = []

            def __index__(self):
                self.hooks.append("index")
                raise AssertionError("index hook")

            def __int__(self):
                self.hooks.append("int")
                raise AssertionError("int hook")

            def __repr__(self):
                self.hooks.append("repr")
                return "secret"

            def __hash__(self):
                self.hooks.append("hash")
                return 1

            def __eq__(self, other):
                self.hooks.append("eq")
                return False

        evil = Evil()
        event = _event(payload={"token": "super-secret"})
        object.__setattr__(event, "duration_ms", evil)
        line = serialize_event(event)
        assert json.loads(line)["recovery_code"] == "EVENT_OVERSIZE"
        assert "super-secret" not in line
        assert evil.hooks == []
