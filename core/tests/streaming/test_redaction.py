"""Canonical bounded recursive redaction tests."""

from __future__ import annotations

import inspect
import json
import math
from pathlib import Path

import pytest

from zana_core.streaming.redaction import (
    REDACTED,
    RedactionLimits,
    Redactor,
    is_sensitive_key,
    redact_value,
    truncate_safe_string,
)


class TestRecursiveRedaction:
    def test_secret_values_removed_recursively(self) -> None:
        payload = {
            "safe": "keep me",
            "authorization": "Bearer secret",
            "nested": {
                "password": "p",
                "cookie": "c",
                "api_key": "k",
                "ok": "value",
            },
            "list": [
                {"token": "t", "safe": 1},
                "plain",
            ],
        }
        result = redact_value(payload)
        assert result["safe"] == "keep me"
        assert result["authorization"] == REDACTED
        assert result["nested"]["password"] == REDACTED
        assert result["nested"]["cookie"] == REDACTED
        assert result["nested"]["api_key"] == REDACTED
        assert result["nested"]["ok"] == "value"
        assert result["list"][0]["token"] == REDACTED
        assert result["list"][0]["safe"] == 1

    def test_sensitive_key_normalization(self) -> None:
        assert is_sensitive_key("Authorization")
        assert is_sensitive_key("x-api-key")
        assert is_sensitive_key("access_token")
        assert not is_sensitive_key("result")
        assert not is_sensitive_key("token_count")
        assert not is_sensitive_key(object())  # type: ignore[arg-type]

    def test_depth_bound(self) -> None:
        value = "x"
        for _ in range(30):
            value = {"next": value}
        result = redact_value(value, RedactionLimits(max_depth=3))
        current = result
        for _ in range(3):
            current = current["next"]
        assert current == REDACTED

    def test_shared_items_budget_across_wide_siblings(self) -> None:
        payload = {f"k{i}": "v" for i in range(1000)}
        result = redact_value(payload, RedactionLimits(max_items=8))
        assert len(result) <= 9
        assert "<redacted-key>" in result

    def test_safe_string_truncated(self) -> None:
        result = redact_value("x" * 100, RedactionLimits(max_string_length=20))
        assert len(result) <= 20
        assert result.endswith("[truncated]")

    def test_utf8_byte_cap(self) -> None:
        value = "é" * 500
        result = redact_value(value, RedactionLimits(max_string_bytes=64))
        assert len(result.encode("utf-8")) <= 64

    def test_absurd_limit_values_rejected(self) -> None:
        with pytest.raises(ValueError):
            RedactionLimits(max_depth=1000)
        with pytest.raises(ValueError):
            RedactionLimits(max_items=10_000_000)
        with pytest.raises(ValueError):
            RedactionLimits(max_container_items=100_000)
        with pytest.raises(ValueError):
            RedactionLimits(max_string_length=100_000)

    def test_redactor_protocol_use(self) -> None:
        redactor = Redactor(RedactionLimits(max_string_length=10))
        assert redactor.redact({"secret": "s"})["secret"] == REDACTED

    def test_limits_boundaries_require_exact_redaction_limits(self) -> None:
        with pytest.raises(TypeError):
            redact_value({"a": 1}, limits=False)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            redact_value({"a": 1}, limits={})  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            Redactor(limits="defaults")  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            truncate_safe_string("x", limits=object())  # type: ignore[arg-type]
        assert redact_value({"a": 1}, limits=None)["a"] == 1


class TestExactBuiltinOnly:
    def test_non_builtin_values_redacted_without_methods(self) -> None:
        called: list[str] = []

        class Hostile:
            def __iter__(self):
                called.append("iter")
                raise AssertionError

            def __len__(self):
                called.append("len")
                raise AssertionError

            def __str__(self):
                called.append("str")
                return "secret"

            def __repr__(self):
                called.append("repr")
                return "secret"

        class HostileDict(dict):
            def items(self):
                called.append("items")
                raise AssertionError

        class HostileIterable:
            def __iter__(self):
                called.append("iterable")
                raise AssertionError

        assert redact_value(Hostile()) == REDACTED
        assert redact_value(HostileDict({"a": 1})) == REDACTED
        assert redact_value(HostileIterable()) == REDACTED
        assert redact_value(Path("/private/secret")) == REDACTED
        assert redact_value(b"raw") == REDACTED
        assert redact_value(bytearray(b"raw")) == REDACTED
        assert redact_value({1, 2}) == REDACTED
        assert redact_value(frozenset({1})) == REDACTED
        assert redact_value(ValueError("private details")) == REDACTED
        assert called == []

    def test_arbitrary_mapping_and_iterable_rejected_inside_containers(self) -> None:
        class Wide(dict):
            pass

        generator = (index for index in range(1000))
        assert redact_value({"custom": Wide({"a": 1})})["custom"] == REDACTED
        assert redact_value({"custom": generator})["custom"] == REDACTED

    def test_nonfinite_floats_redacted(self) -> None:
        assert redact_value({"a": math.nan})["a"] == REDACTED
        assert redact_value({"a": math.inf})["a"] == REDACTED
        assert redact_value({"a": -math.inf})["a"] == REDACTED

    def test_bytes_and_unknown_objects_redacted(self) -> None:
        assert redact_value(b"raw-bytes") == REDACTED
        assert redact_value(object()) == REDACTED
        assert redact_value(ValueError("private details")) == REDACTED


class TestCyclesAliasesAndCollisions:
    def test_cycle_detection(self) -> None:
        value: dict[str, object] = {"name": "x"}
        value["self"] = value
        result = redact_value(value)
        assert result["self"] == {"<redacted-key>": REDACTED}

    def test_list_cycle_detection(self) -> None:
        value: list[object] = ["x"]
        value.append(value)
        assert redact_value(value) == ["x", [REDACTED]]

    def test_repeated_mutable_alias_rejected(self) -> None:
        shared = {"value": 1}
        result = redact_value({"a": shared, "b": shared})
        assert result["a"] == {"value": 1}
        assert result["b"] == {"<redacted-key>": REDACTED}

    def test_deterministic_non_string_key_collision(self) -> None:
        result = redact_value({1: "one", 2: "two"})
        assert result == {"<redacted-key>": REDACTED}

    def test_oversized_key_collapses_deterministically(self) -> None:
        limits = RedactionLimits(max_key_length=8)
        result = redact_value({"k" * 20: "v"}, limits)
        assert result == {"<redacted-key>": REDACTED}

    def test_marker_overwrites_same_named_key_deterministically(self) -> None:
        result = redact_value({1: "one", "<redacted-key>": "user-value"})
        assert result["<redacted-key>"] == REDACTED


class TestBudgets:
    def test_output_byte_budget_allows_one_marker(self) -> None:
        limits = RedactionLimits(max_output_bytes=16, max_items=64)
        result = redact_value({"a": "b", "c": "d", "e": "f"}, limits)
        encoded = len(json.dumps(result, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        marker_bytes = len(
            json.dumps(
                {"<redacted-key>": REDACTED},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        assert encoded <= limits.max_output_bytes + marker_bytes
        assert "<redacted-key>" in result

    def test_item_budget_allows_one_marker(self) -> None:
        limits = RedactionLimits(max_items=4, max_output_bytes=16_384)
        result = redact_value({f"k{i}": "v" for i in range(20)}, limits)
        assert len(result) <= limits.max_items + 1
        assert "<redacted-key>" in result

    def test_key_and_value_utf8_bounds(self) -> None:
        limits = RedactionLimits(max_key_length=4, max_string_bytes=8)
        result = redact_value({"long-key": "value"}, limits)
        assert result == {"<redacted-key>": REDACTED}
        value = redact_value({"safe": "é" * 100}, RedactionLimits(max_string_bytes=8))
        assert len(value["safe"].encode("utf-8")) <= 8

    def test_deterministic_output(self) -> None:
        first = redact_value({"b": 2, "a": {"token": "x"}})
        second = redact_value({"a": {"token": "x"}, "b": 2})
        assert first == second


class TestLegacyMaxLengthBoundary:
    def test_canonical_integer_api_preserved(self) -> None:
        value = "x" * 100
        assert truncate_safe_string(value, 20) == truncate_safe_string(
            value, RedactionLimits(max_string_length=20)
        )
        assert truncate_safe_string("short", max_length=50) == "short"

    def test_bound_sources_are_exact_and_mutually_exclusive(self) -> None:
        with pytest.raises(TypeError):
            truncate_safe_string("x", 20, limits=RedactionLimits())
        with pytest.raises(TypeError):
            truncate_safe_string("x", True)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            truncate_safe_string("x", object())  # type: ignore[arg-type]


class TestInvalidKeyBudget:
    def test_huge_invalid_key_mapping_consumes_item_budget(self, monkeypatch) -> None:
        import zana_core.streaming.redaction as redaction_module

        original = redaction_module._Budget.spend_item
        calls = 0

        def counted(self):
            nonlocal calls
            calls += 1
            return original(self)

        monkeypatch.setattr(redaction_module._Budget, "spend_item", counted)
        payload = {index: f"value-{index}" for index in range(10_000)}
        result = redact_value(
            payload,
            RedactionLimits(max_items=8, max_output_bytes=16_384),
        )
        assert result == {"<redacted-key>": REDACTED}
        assert 2 <= calls <= 10


class TestTrustedLimitsRevalidation:
    def test_model_construct_corruption_rejected_at_every_consumption(self) -> None:
        bad = RedactionLimits.model_construct(max_items="huge")
        with pytest.raises(ValueError):
            redact_value({"a": 1}, bad)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            truncate_safe_string("x" * 100, limits=bad)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            Redactor(limits=bad)  # type: ignore[arg-type]

    def test_out_of_range_exact_fields_rejected(self) -> None:
        bad = RedactionLimits.model_construct(max_depth=0)
        with pytest.raises(ValueError):
            redact_value({}, bad)
        mutated = RedactionLimits()
        object.__setattr__(mutated, "max_items", 10**9)
        with pytest.raises(ValueError):
            redact_value({}, mutated)

    def test_hostile_limits_fields_never_invoke_hooks(self) -> None:
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
        bad = RedactionLimits.model_construct(max_depth=evil)
        with pytest.raises(ValueError):
            redact_value({}, bad)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            Redactor(limits=bad)  # type: ignore[arg-type]
        assert evil.hooks == []

    def test_redactor_does_not_retain_externally_mutable_limits(self) -> None:
        limits = RedactionLimits(max_items=8)
        redactor = Redactor(limits)
        object.__setattr__(limits, "max_items", 0)
        result = redactor.redact({"a": 1, "b": 2, "c": 3})
        assert result == {"a": 1, "b": 2, "c": 3}


class TestCanonicalIntegerBound:
    def test_oversized_exact_int_redacted_before_serialization(self) -> None:
        assert redact_value({"n": 2**63})["n"] == REDACTED
        assert redact_value({"n": -(2**63) - 1})["n"] == REDACTED
        assert redact_value({"n": 2**63 - 1})["n"] == 2**63 - 1
        assert redact_value({"n": -(2**63)})["n"] == -(2**63)


class TestPaths:
    def test_posix_and_windows_paths_redacted_on_every_host(self) -> None:
        payload = {
            "path": "/private/full/document.md",
            "file": "C:\\Users\\Secret\\doc.md",
            "filename": "\\\\server\\share\\private\\file.txt",
            "file_path": "C:/Users/Secret/other.md",
            "directory": "relative/folder/name.txt",
            "root": "/private/dir/",
            "source": "filename.md",
            "safe": "kept",
        }
        result = redact_value(payload)
        assert result["path"] == "document.md"
        assert result["file"] == "doc.md"
        assert result["filename"] == "file.txt"
        assert result["file_path"] == "other.md"
        assert result["directory"] == "name.txt"
        assert result["root"] == "dir"
        assert result["source"] == "filename.md"
        assert result["safe"] == "kept"

    def test_path_basename_or_hash(self) -> None:
        long = "x" * 300
        result = redact_value({"path": f"/{long}/{long}.md"})
        assert "/" not in result["path"]
        assert result["path"].startswith("path-")
        assert redact_value({"path": Path("/private/doc.md")})["path"] == REDACTED

    def test_huge_path_only_scans_bounded_prefix(self, monkeypatch) -> None:
        import zana_core.streaming.redaction as redaction_module

        original_digest = redaction_module._path_digest
        encoded: list[int] = []

        def counting_digest(value: str, length: int) -> str:
            assert len(value) <= redaction_module.MAX_PATH_INPUT_BYTES
            encoded.append(len(value))
            return original_digest(value, length)

        monkeypatch.setattr(redaction_module, "_path_digest", counting_digest)
        huge = "x" * 10_000_000
        result = redact_value({"path": huge})
        assert result["path"].startswith("path-")
        assert encoded
        assert all(size <= redaction_module.MAX_PATH_INPUT_BYTES for size in encoded)

    def test_path_digest_marks_exact_oversize_length(self, monkeypatch) -> None:
        import zana_core.streaming.redaction as redaction_module

        original_digest = redaction_module._path_digest

        def counting_digest(value: str, length: int) -> str:
            assert len(value) <= redaction_module.MAX_PATH_INPUT_BYTES
            return original_digest(value, length)

        monkeypatch.setattr(redaction_module, "_path_digest", counting_digest)
        result = redact_value({"path": "p" * 100_000})
        expected = original_digest("p" * 512, 100_000)
        assert result["path"] == expected

    def test_huge_path_encoding_budget_proof(self, monkeypatch) -> None:
        import zana_core.streaming.redaction as redaction_module

        original = redaction_module._path_digest
        seen: list[int] = []

        def tracking_digest(value: str, length: int) -> str:
            seen.append(len(value))
            return original(value, length)

        monkeypatch.setattr(redaction_module, "_path_digest", tracking_digest)
        result = redaction_module._safe_path_value("x" * 10_000_000)
        assert result.startswith("path-")
        assert seen
        assert all(size <= redaction_module.MAX_PATH_INPUT_BYTES for size in seen)


class TestContentAndSensitiveKeys:
    def test_content_keys_redacted(self) -> None:
        payload = {
            "prompt": "private prompt",
            "response": "private response",
            "completion": "private completion",
            "document": "private document",
            "document_content": "private",
            "content": "private",
            "raw": "private",
            "raw_body": "private",
            "request_body": "private",
            "response_body": "private",
            "environment": "private",
            "env": "private",
        }
        result = redact_value(payload)
        for key in payload:
            assert result[key] == REDACTED

    def test_safe_operational_fields_preserved(self) -> None:
        payload = {
            "status": "ok",
            "message": "operation completed",
            "recovery_code": "RETRY",
            "count": 3,
            "duration_ms": 5,
        }
        result = redact_value(payload)
        assert result["status"] == "ok"
        assert result["message"] == "operation completed"
        assert result["recovery_code"] == "RETRY"
        assert result["count"] == 3

    def test_huge_sensitive_value_never_truncated(self) -> None:
        result = redact_value({"token": "s" * 100_000})
        assert result["token"] == REDACTED


class TestCanonicalBoundary:
    def test_observability_has_no_duplicate_redactor_logic(self) -> None:
        import zana_core.observability.redact as observability_redact

        source = inspect.getsource(observability_redact)
        assert "def _redact_value" not in source
        assert "def _redact_mapping" not in source
        assert "def _redact_sequence" not in source
