"""Diagnostic model immutability and redaction tests."""

from __future__ import annotations

import pytest

from zana_core.diagnostics.models import (
    CheckStatus,
    DiagnosticCheck,
    DiagnosticIssue,
    Evidence,
    Severity,
)


class TestRedaction:
    def test_evidence_never_contains_full_path_or_secret(self) -> None:
        evidence = Evidence(
            observed_source="config",
            basename="db.sqlite3",
            digest_prefix="sha256:abc",
            boolean_presence=True,
        )
        rendered = evidence.model_dump_json()
        assert "/Users/secret" not in rendered
        assert "launch-token" not in rendered
        assert "db.sqlite3" in rendered

    def test_check_is_immutable(self) -> None:
        check = DiagnosticCheck(
            check_id="c1",
            name="check",
            status=CheckStatus.PASS,
            severity=Severity.INFO,
            duration_seconds=0.1,
            observed_source="test",
            evidence=Evidence(observed_source="test", boolean_presence=True),
            issues=[
                DiagnosticIssue(
                    code="NONE",
                    severity=Severity.INFO,
                    message="ok",
                    recovery_actions=[],
                )
            ],
        )
        with pytest.raises(ValueError):
            check.status = CheckStatus.FAIL
