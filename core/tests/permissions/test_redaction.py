"""Denial redaction: no secret values or private document contents leak."""

from __future__ import annotations

from pathlib import Path

from zana_core.permissions.decisions import PermissionDecisionEngine
from zana_core.permissions.loader import load_policy
from zana_core.permissions.redaction import redact_path, redact_reference, redact_value


class TestRedactionHelpers:
    def test_values_are_replaced_with_placeholder(self) -> None:
        assert redact_value("super-secret-value") == "***"
        assert redact_value({"token": "super-secret-value"}) == "***"

    def test_paths_reduce_to_basename(self) -> None:
        assert redact_path("/private/data/documents/secret-notes.md") == "secret-notes.md"

    def test_references_are_short_identifiers(self) -> None:
        assert redact_reference("api_key") == "api_key"
        assert redact_reference("  ") == "***"


class TestDenialRedaction:
    def test_secret_denial_never_contains_secret_value(self) -> None:
        engine = PermissionDecisionEngine(load_policy("schemaVersion: 1\n"))
        denial = engine.secret_denial("api_key")
        payload = denial.model_dump()
        serialized = str(payload)
        assert "super-secret-value" not in serialized
        assert denial.target == "api_key"
        assert "secret value was exposed" in denial.message

    def test_tool_denial_is_structured(self) -> None:
        engine = PermissionDecisionEngine(load_policy("schemaVersion: 1\n"))
        denial = engine.tool_denial("zana.calculator")
        assert denial.code == "TOOL_NOT_ALLOWED"
        assert denial.decision.value == "deny"
        assert denial.redacted is True

    def test_filesystem_denial_uses_basename_only(self, tmp_path: Path) -> None:
        root = tmp_path / "mount"
        root.mkdir()
        engine = PermissionDecisionEngine(
            load_policy("schemaVersion: 1\nfilesystem:\n  read: []\n")
        )
        private_document = root / "private-notes.md"
        denial = engine.filesystem_denial("filesystem_read", private_document)
        assert "private-notes.md" in denial.target
        assert str(root) not in denial.message
        assert "private" not in denial.message
