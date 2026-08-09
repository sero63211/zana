"""Strict exact Event fields and pre-Pydantic bounded immutable payload tests."""

from __future__ import annotations

import json
import math
import threading
import types
from pathlib import Path

import pytest
from pydantic import ValidationError

from zana_core.observability.events import (
    MAX_AGGREGATE_BYTES,
    MAX_DEPTH,
    MAX_DURATION_MS,
    MAX_LIST_SIZE,
    MAX_MAP_SIZE,
    MAX_PAYLOAD_INT,
    MAX_SCHEMA_VERSION,
    MAX_STRING_LENGTH,
    MIN_PAYLOAD_INT,
    Event,
    EventContext,
    EventKind,
    Severity,
    _FrozenDict,
    payload_to_builtin,
)
from zana_core.observability.serialization import serialize_event


def _event(**overrides) -> Event:
    defaults = {
        "kind": EventKind.SYSTEM,
        "severity": Severity.INFO,
        "message": "event",
        "payload": {},
    }
    defaults.update(overrides)
    return Event(**defaults)


class TestStrictExactFields:
    def test_bool_rejected_as_int(self) -> None:
        with pytest.raises(ValidationError):
            _event(schema_version=True)
        with pytest.raises(ValidationError):
            _event(duration_ms=True)

    def test_float_coercion_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _event(duration_ms=1.5)
        with pytest.raises(ValidationError):
            _event(duration_ms="1")

    def test_int_coercion_to_float_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _event(progress_0_1=1)
        with pytest.raises(ValidationError):
            _event(progress_0_1="0.5")

    def test_nan_and_infinity_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _event(progress_0_1=float("nan"))
        with pytest.raises(ValidationError):
            _event(progress_0_1=float("inf"))

    def test_required_fields_reject_none(self) -> None:
        with pytest.raises(ValidationError):
            _event(message=None)
        with pytest.raises(ValidationError):
            _event(kind=None)
        with pytest.raises(ValidationError):
            _event(severity=None)

    def test_schema_and_duration_have_hard_maxima(self) -> None:
        with pytest.raises(ValidationError):
            _event(schema_version=MAX_SCHEMA_VERSION + 1)
        with pytest.raises(ValidationError):
            _event(schema_version="1")
        with pytest.raises(ValidationError):
            _event(duration_ms=MAX_DURATION_MS + 1)
        with pytest.raises(ValidationError):
            _event(duration_ms=False)

    def test_timestamp_must_be_exact_aware_utc_datetime(self) -> None:
        from datetime import UTC, datetime

        event = _event(timestamp=datetime.now(UTC))
        assert event.timestamp.tzinfo is not None
        with pytest.raises(ValidationError):
            _event(timestamp="2026-01-01T00:00:00Z")
        with pytest.raises(ValidationError):
            _event(timestamp=datetime.now())

    def test_hostile_tzinfo_hook_never_invoked(self) -> None:
        from datetime import UTC, datetime, tzinfo

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
        hostile = datetime(2026, 1, 1, tzinfo=evil)
        with pytest.raises(ValidationError):
            _event(timestamp=hostile)
        assert evil.hooks == []

        event = _event(timestamp=datetime(2026, 1, 1, tzinfo=UTC))
        object.__setattr__(event, "timestamp", hostile)
        line = serialize_event(event)
        assert '"recovery_code":"EVENT_OVERSIZE"' in line
        assert evil.hooks == []

    def test_identifier_control_characters_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _event(operation_id="bad\nid")
        with pytest.raises(ValidationError):
            _event(job_id="bad\x00id")
        with pytest.raises(ValidationError):
            _event(phase="bad\rid")
        with pytest.raises(ValidationError):
            _event(context=EventContext(instance_id="bad\x1fid"))


class TestPayloadBounds:
    def test_payload_must_be_exact_dict(self) -> None:
        with pytest.raises(ValidationError):
            _event(payload=[1, 2])
        with pytest.raises(ValidationError):
            _event(payload="payload")
        with pytest.raises(ValidationError):
            _event(payload=None)

    def test_dict_subclass_rejected(self) -> None:
        class Hostile(dict):
            def items(self):
                raise AssertionError("hostile items must not be called")

        with pytest.raises(ValidationError):
            _event(payload=Hostile())

    def test_list_subclass_rejected(self) -> None:
        class Hostile(list):
            def __iter__(self):
                raise AssertionError("hostile iteration must not be called")

        with pytest.raises(ValidationError):
            _event(payload={"items": Hostile([1])})

    def test_arbitrary_mapping_iterable_and_path_rejected(self) -> None:
        class HostileMapping:
            def __len__(self):
                raise AssertionError("hostile len must not be called")

            def items(self):
                raise AssertionError("hostile items must not be called")

        class HostileIterable:
            def __iter__(self):
                raise AssertionError("hostile iteration must not be called")

        with pytest.raises(ValidationError):
            _event(payload={"mapping": HostileMapping()})
        with pytest.raises(ValidationError):
            _event(payload={"iterable": HostileIterable()})
        with pytest.raises(ValidationError):
            _event(payload={"path": Path("/private/secret")})
        with pytest.raises(ValidationError):
            _event(payload={"bytes": b"secret"})
        with pytest.raises(ValidationError):
            _event(payload={"unordered": {1, 2}})

    def test_model_dump_object_rejected(self) -> None:
        class Hostile:
            def model_dump(self):
                raise AssertionError("model_dump must not be called")

        with pytest.raises(ValidationError):
            _event(payload={"model": Hostile()})

    def test_non_finite_and_string_coercion_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _event(payload={"nan": math.nan})
        with pytest.raises(ValidationError):
            _event(payload={"inf": math.inf})

    def test_non_string_keys_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _event(payload={1: "one"})
        with pytest.raises(ValidationError):
            _event(payload={True: "yes"})

    def test_huge_key_and_value_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _event(payload={"k" * 600: "v"})
        with pytest.raises(ValidationError):
            _event(payload={"k": "v" * 600})

    def test_depth_item_and_map_bounds_rejected(self) -> None:
        deep = {}
        current = deep
        for _ in range(MAX_DEPTH + 1):
            current["next"] = {}
            current = current["next"]
        with pytest.raises(ValidationError):
            _event(payload=deep)
        with pytest.raises(ValidationError):
            _event(payload={f"k{i}": i for i in range(MAX_MAP_SIZE + 1)})
        with pytest.raises(ValidationError):
            _event(payload={"items": list(range(MAX_LIST_SIZE + 1))})

    def test_aggregate_byte_bound_rejected(self) -> None:
        payload = {f"key-{index}": "x" * 100 for index in range(64)}
        assert sum(len(f"key-{index}") + 100 for index in range(64)) > MAX_AGGREGATE_BYTES
        with pytest.raises(ValidationError):
            _event(payload=payload)

    def test_cycle_and_repeated_alias_rejected(self) -> None:
        cyclic: dict[str, object] = {"name": "x"}
        cyclic["self"] = cyclic
        with pytest.raises(ValidationError):
            _event(payload=cyclic)
        shared = {"value": 1}
        with pytest.raises(ValidationError):
            _event(payload={"a": shared, "b": shared})


class TestPayloadImmutability:
    def test_payload_is_deeply_immutable(self) -> None:
        event = _event(payload={"a": 1, "nested": {"b": [1, 2]}, "path": "/private/doc.md"})
        with pytest.raises(TypeError):
            event.payload["a"] = 2  # type: ignore[index]
        with pytest.raises(TypeError):
            event.payload["nested"]["b"] = [3]  # type: ignore[index]
        with pytest.raises(TypeError):
            event.payload["new"] = "x"  # type: ignore[index]

    def test_payload_backing_state_cannot_be_reassigned_or_mutated(self) -> None:
        event = _event(payload={"a": 1})
        with pytest.raises(AttributeError):
            event.payload._items = (("mutated", object()),)  # type: ignore[attr-defined]
        with pytest.raises(AttributeError):
            object.__setattr__(event.payload, "_items", (("mutated", object()),))
        with pytest.raises(TypeError):
            event.payload["a"] = 2  # type: ignore[index]
        with pytest.raises(TypeError):
            event.payload |= {"b": 2}  # type: ignore[operator]
        line = serialize_event(event)
        assert json.loads(line)["payload"] == {"a": 1}

    def test_arbitrary_objects_cannot_be_injected_through_private_mapping(self) -> None:
        event = _event(payload={"a": 1})
        hostile = types.MappingProxyType({"bad": object()})
        object.__setattr__(event, "payload", hostile)
        line = serialize_event(event)
        assert len(line.encode("utf-8")) <= 8192
        assert "0x" not in line

    def test_model_construct_rejects_hostile_frozen_mapping(self) -> None:
        hostile = types.MappingProxyType({"bad": object()})
        with pytest.raises(ValueError):
            Event.model_construct(
                kind=EventKind.SYSTEM,
                severity=Severity.INFO,
                message="x",
                payload=hostile,
            )

    def test_frozen_mapping_private_construction_is_validated(self) -> None:
        with pytest.raises(ValueError):
            _FrozenDict([("bad", object())])
        with pytest.raises(ValueError):
            _FrozenDict([(1, "bad")])
        with pytest.raises(ValueError):
            _FrozenDict([("bad", [1, 2])])
        with pytest.raises(ValueError):
            _FrozenDict("not-items")
        frozen = _FrozenDict([("ok", 1)])
        assert frozen["ok"] == 1
        assert frozen == {"ok": 1}

    def test_frozen_mapping_tuple_index_and_slice_behavior_preserved(self) -> None:
        frozen = _FrozenDict([("a", 1), ("b", 2)])
        assert frozen["a"] == 1
        assert frozen[0] == ("a", 1)
        assert frozen[0:2] == (("a", 1), ("b", 2))
        with pytest.raises(TypeError):
            frozen[True]  # type: ignore[index]

    def test_frozen_mapping_rejects_oversized_key_and_map(self) -> None:
        with pytest.raises(ValueError):
            _FrozenDict([("k" * 1025, 1)])
        with pytest.raises(ValueError):
            _FrozenDict([("k" * (MAX_STRING_LENGTH + 1), 1)])
        with pytest.raises(ValueError):
            _FrozenDict([("é" * 513, 1)])
        accepted = _FrozenDict([(str(index), index) for index in range(MAX_MAP_SIZE)])
        assert len(accepted) == MAX_MAP_SIZE
        with pytest.raises(ValueError):
            _FrozenDict([(str(index), index) for index in range(MAX_MAP_SIZE + 1)])

    def test_frozen_mapping_rejects_duplicate_keys(self) -> None:
        with pytest.raises(ValueError):
            _FrozenDict([("a", 1), ("a", 2)])

    def test_frozen_mapping_aggregate_byte_bound(self) -> None:
        small = _FrozenDict([("ok", "x")])
        assert payload_to_builtin(small) == {"ok": "x"}
        oversized = [(f"key-{index}", "x" * 200) for index in range(MAX_MAP_SIZE)]
        with pytest.raises(ValueError):
            _FrozenDict(oversized)

    def test_base_constructor_bypass_fails_closed_before_conversion(self) -> None:
        invalid = tuple.__new__(_FrozenDict, (("bad", object()),))
        with pytest.raises(ValueError):
            payload_to_builtin(invalid)
        with pytest.raises(ValueError):
            Event.model_construct(
                kind=EventKind.SYSTEM,
                severity=Severity.INFO,
                message="x",
                payload=invalid,
            )

    def test_base_constructor_bypass_huge_map_fails_closed(self) -> None:
        invalid = tuple.__new__(
            _FrozenDict,
            tuple((str(index), index) for index in range(MAX_MAP_SIZE + 1)),
        )
        with pytest.raises(ValueError):
            payload_to_builtin(invalid)

    def test_base_constructor_bypass_nested_invalid_fails_closed(self) -> None:
        invalid = tuple.__new__(
            _FrozenDict,
            (("nested", tuple.__new__(_FrozenDict, (("bad", object()),))),),
        )
        with pytest.raises(ValueError):
            payload_to_builtin(invalid)

    def test_equality_never_invokes_hostile_value_hooks(self) -> None:
        invoked: list[str] = []

        class EvilEq:
            def __eq__(self, other):
                invoked.append("eq")
                raise RuntimeError("hostile equality invoked")

        result = _FrozenDict([("a", 1)]) == {"a": EvilEq()}
        assert result is False
        assert invoked == []

    def test_equality_invalid_nested_operand_returns_false(self) -> None:
        result = _FrozenDict([("a", 1)]) == {"nested": object()}
        assert result is False

        class HostileList(list):
            pass

        result = _FrozenDict([("a", 1)]) == {"items": HostileList([1])}
        assert result is False

    def test_equality_preserves_valid_exact_payload_comparison(self) -> None:
        event = _event(payload={"a": 1, "nested": {"b": [1, 2]}})
        assert event.payload == {"a": 1, "nested": {"b": [1, 2]}}
        assert _FrozenDict([("a", 1)]) == _FrozenDict([("a", 1)])
        assert _FrozenDict([("a", 1)]) != {"a": 2}

    def test_equality_root_len_gate_prevents_materialization(self, monkeypatch) -> None:
        real_new = _FrozenDict.__new__
        seen_lengths: list[int] = []

        def tracked_new(cls, items):
            seen_lengths.append(len(items))
            return real_new(cls, items)

        monkeypatch.setattr(_FrozenDict, "__new__", tracked_new)
        oversized = {f"k{index}": index for index in range(MAX_MAP_SIZE + 1)}
        result = _FrozenDict([("a", 1)]) == oversized
        assert result is False
        assert seen_lengths
        assert all(length <= MAX_MAP_SIZE for length in seen_lengths)

    def test_equality_cap_plus_one_returns_false(self) -> None:
        oversized = {f"k{index}": index for index in range(MAX_MAP_SIZE + 1)}
        assert (_FrozenDict([("a", 1)]) == oversized) is False

    def test_concurrent_construction_and_reads_are_bounded(self) -> None:
        errors: list[Exception] = []

        def worker(offset: int) -> None:
            try:
                for index in range(25):
                    event = _event(
                        payload={"n": index, "token": "secret", "nested": {"v": offset}},
                    )
                    assert dict(event.payload.items())["n"] == index
                    line = serialize_event(event)
                    assert "secret" not in line
            except Exception as error:  # noqa: BLE001
                errors.append(error)

        threads = [threading.Thread(target=worker, args=(offset,)) for offset in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []

    def test_payload_equality_and_reads(self) -> None:
        event = _event(payload={"a": 1, "nested": {"b": "x"}})
        assert event.payload == {"a": 1, "nested": {"b": "x"}}
        assert event.payload["a"] == 1
        assert set(event.payload.keys()) == {"a", "nested"}

    def test_post_construction_mutation_is_not_aliased_into_snapshot(self) -> None:
        raw: dict[str, object] = {"token": "secret"}
        event = _event(payload=raw)
        line = serialize_event(event)
        assert "secret" not in line
        raw["token"] = "changed"
        assert "changed" not in line

    def test_hostile_post_construction_payload_falls_back(self) -> None:
        event = _event(payload={"ok": True})
        object.__setattr__(event, "payload", {"token": "super-secret"})
        line = serialize_event(event)
        assert "super-secret" not in line
        assert '"recovery_code":"EVENT_OVERSIZE"' in line


class TestPayloadIntegerBound:
    def test_out_of_range_integer_rejected_before_conversion(self) -> None:
        with pytest.raises(ValidationError):
            _event(payload={"n": MAX_PAYLOAD_INT + 1})
        with pytest.raises(ValidationError):
            _event(payload={"n": MIN_PAYLOAD_INT - 1})

    def test_boundary_integers_accepted(self) -> None:
        event = _event(payload={"min": MIN_PAYLOAD_INT, "max": MAX_PAYLOAD_INT})
        assert event.payload["min"] == MIN_PAYLOAD_INT
        assert event.payload["max"] == MAX_PAYLOAD_INT

    def test_constructor_and_base_bypass_reject_huge_integers(self) -> None:
        with pytest.raises(ValueError):
            _FrozenDict([("n", 10**1000)])
        invalid = tuple.__new__(_FrozenDict, (("n", 10**1000),))
        with pytest.raises(ValueError):
            payload_to_builtin(invalid)
