"""Redaction adapter tests: exact builtins, exact Event, paths, and bounds."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from zana_core.observability.events import (
    MAX_DURATION_MS,
    MAX_SCHEMA_VERSION,
    MAX_STRING_LENGTH,
    Event,
    EventContext,
    EventKind,
    Severity,
    _FrozenDict,
)
from zana_core.observability.redact import redact_event, redact_object


def _event(**overrides) -> Event:
    defaults = {
        "kind": EventKind.SYSTEM,
        "severity": Severity.INFO,
        "message": "event",
        "payload": {},
    }
    defaults.update(overrides)
    return Event(**defaults)


class TestRedactObject:
    def test_secret_keys_case_insensitive(self) -> None:
        for key in ("token", "TOKEN", "ApiKey", "password", "SECRET", "credential"):
            assert redact_object({"value": "super-secret", key: "hidden"})[key] == "***"

    def test_nested_secrets_redacted(self) -> None:
        payload = {"config": {"auth": {"api_key": "hidden", "host": "ok"}}}
        redacted = redact_object(payload)
        assert redacted["config"]["auth"]["api_key"] == "***"
        assert redacted["config"]["auth"]["host"] == "ok"

    def test_cycle_safe(self) -> None:
        value: dict[str, object] = {"name": "x"}
        value["self"] = value
        redacted = redact_object(value)
        assert redacted["self"] == {"<redacted-key>": "***"}

    def test_exact_builtin_containers_only(self) -> None:
        class Wide(dict):
            pass

        class Hostile:
            def __iter__(self):
                raise AssertionError

        assert redact_object(Wide({"a": 1})) == "***"
        assert redact_object(Hostile()) == "***"
        assert redact_object(Path("/private/secret-document.md")) == "***"
        assert redact_object(b"raw-bytes") == "***"
        assert redact_object({1, 2}) == "***"

    def test_sensitive_content_keys_redacted(self) -> None:
        payload = {
            "prompt": "private prompt",
            "response": "private response",
            "document": "private document",
            "content": "private content",
            "raw": "private raw",
            "body": "private body",
            "environment": "private env",
        }
        redacted = redact_object(payload)
        for key in payload:
            assert redacted[key] == "***"

    def test_safe_operational_fields_preserved(self) -> None:
        payload = {
            "status": "ok",
            "recovery_code": "RETRY",
            "operation_id": "op-1",
            "count": 3,
            "duration_ms": 5,
        }
        redacted = redact_object(payload)
        assert redacted["status"] == "ok"
        assert redacted["recovery_code"] == "RETRY"
        assert redacted["operation_id"] == "op-1"
        assert redacted["count"] == 3

    def test_windows_paths_redacted(self) -> None:
        redacted = redact_object(
            {"path": "C:\\Users\\Private\\secret.txt", "source": "\\\\host\\share\\file.txt"}
        )
        assert redacted["path"] == "secret.txt"
        assert redacted["source"] == "file.txt"


class TestRedactEvent:
    def test_event_payload_redacted_and_safe_fields_preserved(self) -> None:
        event = _event(
            message="hello",
            operation_id="op-1",
            job_id="job-1",
            phase="phase-1",
            payload={"ok": True, "token": "super-secret", "path": "/private/doc.md"},
        )
        redacted = redact_event(event)
        assert redacted["message"] == "hello"
        assert redacted["operation_id"] == "op-1"
        assert redacted["payload"]["ok"] is True
        assert redacted["payload"]["token"] == "***"
        assert redacted["payload"]["path"] == "doc.md"
        assert redacted["kind"] == "system"
        assert redacted["severity"] == "info"

    def test_exact_event_required(self) -> None:
        with pytest.raises(TypeError):
            redact_event(object())
        with pytest.raises(TypeError):
            redact_event({"kind": "system"})

    def test_model_dump_is_never_used(self) -> None:
        called: list[str] = []

        class Hostile:
            def model_dump(self):
                called.append("model_dump")
                return {"token": "secret"}

            def __repr__(self):
                called.append("repr")
                return "secret"

        with pytest.raises(TypeError):
            redact_event(Hostile())
        assert called == []

    def test_hostile_payload_raises_generic_value_error(self) -> None:
        event = _event(payload={"ok": True})
        object.__setattr__(event, "payload", {"token": "secret", "nested": {"a": 1}})
        with pytest.raises(ValueError):
            redact_event(event)

    def test_payload_is_fresh_immutable_trusted_mapping(self) -> None:
        event = _event(payload={"a": {"b": [1, 2]}})
        redacted = redact_event(event)
        assert redacted["payload"] == {"a": {"b": [1, 2]}}


class TestCorruptedExactEvents:
    def test_hostile_tzinfo_hook_never_invoked(self) -> None:
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
        event = _event()
        object.__setattr__(event, "timestamp", datetime(2026, 1, 1, tzinfo=evil))
        with pytest.raises(ValueError):
            redact_event(event)
        assert evil.hooks == []

    def test_model_construct_field_ranges_rejected(self) -> None:
        cases = [
            {"schema_version": 0},
            {"schema_version": MAX_SCHEMA_VERSION + 1},
            {"progress_0_1": 1.5},
            {"progress_0_1": -0.1},
            {"duration_ms": -1},
            {"duration_ms": MAX_DURATION_MS + 1},
            {"operation_id": "x" * 129},
        ]
        for overrides in cases:
            event = Event.model_construct(
                kind=EventKind.SYSTEM,
                severity=Severity.INFO,
                payload={},
                **overrides,
            )
            with pytest.raises(ValueError):
                redact_event(event)

    def test_model_construct_utf8_message_bound_rejected(self) -> None:
        event = Event.model_construct(
            kind=EventKind.SYSTEM,
            severity=Severity.INFO,
            message="é" * (MAX_STRING_LENGTH + 1),
            payload={},
        )
        with pytest.raises(ValueError):
            redact_event(event)

    def test_object_setattr_field_ranges_rejected(self) -> None:
        event = _event()
        object.__setattr__(event, "schema_version", 0)
        with pytest.raises(ValueError):
            redact_event(event)
        event = _event()
        object.__setattr__(event, "duration_ms", -1)
        with pytest.raises(ValueError):
            redact_event(event)
        event = _event()
        object.__setattr__(event, "message", "é" * (MAX_STRING_LENGTH + 1))
        with pytest.raises(ValueError):
            redact_event(event)

    def test_corrupted_event_context_rejected(self) -> None:
        context = EventContext.model_construct(operation_id="bad\nid")
        event = Event.model_construct(
            kind=EventKind.SYSTEM,
            severity=Severity.INFO,
            message="x",
            context=context,
        )
        with pytest.raises(ValueError):
            redact_event(event)

    def test_base_constructed_frozen_payload_rejected(self) -> None:
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
        event = _event()
        object.__setattr__(event, "schema_version", evil)
        with pytest.raises(ValueError):
            redact_event(event)
        assert evil.hooks == []
