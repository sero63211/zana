"""Fail-closed gate engine tests: absolute, improvement, regression."""

from __future__ import annotations

import pytest

from zana_core.evaluation.gates import VerificationGateEngine
from zana_core.evaluation.models import (
    AggregateMetrics,
    GateDecision,
    VerificationStatus,
)


def _metrics(pass_rate: float, *, invalid: int = 0) -> AggregateMetrics:
    return AggregateMetrics(
        cases=10,
        passed=round(pass_rate * 10),
        failed=10 - round(pass_rate * 10),
        pass_rate=pass_rate,
        invalid=invalid,
    )


class TestGateEngine:
    def test_all_gates_pass_verifies_local(self) -> None:
        baseline = _metrics(0.52)
        candidate = _metrics(0.79)
        results, status = (
            VerificationGateEngine()
            .absolute("domain_absolute", 0.70)
            .improvement("domain_improvement", 0.10)
            .max_drop("regression_max_drop", 0.03)
            .evaluate(baseline, candidate)
        )
        assert status == VerificationStatus.VERIFIED_LOCAL
        assert all(result.decision == GateDecision.PASS for result in results)

    def test_absolute_gate_fails(self) -> None:
        _, status = (
            VerificationGateEngine()
            .absolute("domain_absolute", 0.90)
            .evaluate(_metrics(0.52), _metrics(0.79))
        )
        assert status == VerificationStatus.VERIFICATION_FAILED

    def test_improvement_gate_fails(self) -> None:
        results, status = (
            VerificationGateEngine()
            .improvement("domain_improvement", 0.20)
            .evaluate(_metrics(0.52), _metrics(0.60))
        )
        assert status == VerificationStatus.VERIFICATION_FAILED
        assert results[0].decision == GateDecision.FAIL
        assert results[0].observed == pytest.approx(0.08)

    def test_regression_prevention(self) -> None:
        results, status = (
            VerificationGateEngine()
            .max_drop("regression_max_drop", 0.01)
            .evaluate(_metrics(0.88), _metrics(0.85))
        )
        assert status == VerificationStatus.VERIFICATION_FAILED
        assert results[0].decision == GateDecision.FAIL

    def test_acceptable_regression_passes(self) -> None:
        _, status = (
            VerificationGateEngine()
            .max_drop("regression_max_drop", 0.05)
            .evaluate(_metrics(0.88), _metrics(0.85))
        )
        assert status == VerificationStatus.VERIFIED_LOCAL

    def test_invalid_measurements_fail_closed(self) -> None:
        _, status = (
            VerificationGateEngine()
            .absolute("domain_absolute", 0.7)
            .evaluate(_metrics(0.5, invalid=1), _metrics(0.8))
        )
        assert status == VerificationStatus.VERIFICATION_FAILED

    def test_empty_suite_fails_closed(self) -> None:
        empty = AggregateMetrics(cases=0, passed=0, failed=0, pass_rate=0.0)
        _, status = (
            VerificationGateEngine().absolute("domain_absolute", 0.7).evaluate(empty, _metrics(0.8))
        )
        assert status == VerificationStatus.VERIFICATION_FAILED

    def test_no_declared_gates_fail_closed(self) -> None:
        results, status = VerificationGateEngine().evaluate(_metrics(0.9), _metrics(0.95))
        assert status == VerificationStatus.VERIFICATION_FAILED
        assert results[0].name == "no_gates"
