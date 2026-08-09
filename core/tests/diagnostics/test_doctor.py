"""DoctorService budget, timeout, and aggregate health tests."""

from __future__ import annotations

import pytest

from zana_core.diagnostics.doctor import BudgetExceededError, DoctorService
from zana_core.diagnostics.models import (
    AggregateHealth,
    CheckStatus,
    DiagnosticCheck,
    DiagnosticIssue,
    Evidence,
    ProbeBudget,
    Severity,
)
from zana_core.diagnostics.probes import ProbeTimeoutError


class _FakeClock:
    def __init__(self) -> None:
        self.value = 1.0

    def __call__(self) -> float:
        self.value += 1.0
        return self.value


class FakeProbe:
    def __init__(self, check_id: str, status: CheckStatus = CheckStatus.PASS) -> None:
        self.check_id = check_id
        self.name = check_id
        self.status = status
        self.issues: list[DiagnosticIssue] = []

    def run(self, budget: ProbeBudget) -> DiagnosticCheck:
        return DiagnosticCheck(
            check_id=self.check_id,
            name=self.name,
            status=self.status,
            severity=Severity.INFO,
            duration_seconds=0.01,
            observed_source="fake",
            evidence=Evidence(observed_source="fake", boolean_presence=True),
            issues=list(self.issues),
        )


class SlowProbe:
    check_id = "slow"
    name = "slow"

    def run(self, budget: ProbeBudget) -> DiagnosticCheck:
        raise ProbeTimeoutError("slow probe")


class InstantProbe:
    check_id = "instant"
    name = "instant"

    def run(self, budget: ProbeBudget) -> DiagnosticCheck:
        return DiagnosticCheck(
            check_id=self.check_id,
            name=self.name,
            status=CheckStatus.PASS,
            severity=Severity.INFO,
            duration_seconds=0.0,
            observed_source="fake",
            evidence=Evidence(observed_source="fake", boolean_presence=True),
        )


class ExceptionProbe:
    check_id = "boom"
    name = "boom"

    def run(self, budget: ProbeBudget) -> DiagnosticCheck:
        raise RuntimeError("boom")


class TestDoctorService:
    def test_max_check_count_enforced(self) -> None:
        service = DoctorService(budget=ProbeBudget(max_checks=2))
        with pytest.raises(BudgetExceededError):
            service.run([FakeProbe("a"), FakeProbe("b"), FakeProbe("c")])

    def test_total_budget_skips_remaining(self) -> None:
        budget = ProbeBudget(total_budget_seconds=0.1)
        service = DoctorService(
            budget=budget,
            clock=_FakeClock(),
        )
        report = service.run([InstantProbe(), InstantProbe()])
        assert any(item.status == CheckStatus.SKIPPED for item in report.checks)

    def test_probe_exception_is_unavailable_not_crash(self) -> None:
        service = DoctorService()
        report = service.run([ExceptionProbe()])
        assert report.checks[0].status == CheckStatus.UNAVAILABLE
        assert report.aggregate_health == AggregateHealth.HEALTHY

    def test_fail_aggregates_to_failed(self) -> None:
        failing = FakeProbe("fail", status=CheckStatus.FAIL)
        failing.issues = [
            DiagnosticIssue(
                code="MANDATORY",
                severity=Severity.ERROR,
                message="mandatory failure",
                recovery_actions=[],
            )
        ]
        report = DoctorService().run([failing])
        assert report.aggregate_health == AggregateHealth.FAILED

    def test_warn_aggregates_to_limited_features(self) -> None:
        warning = FakeProbe("warn", status=CheckStatus.WARN)
        report = DoctorService().run([warning])
        assert report.aggregate_health == AggregateHealth.PASS_WITH_LIMITED_FEATURES

    def test_healthy_when_all_pass(self) -> None:
        report = DoctorService().run([FakeProbe("a"), FakeProbe("b")])
        assert report.aggregate_health == AggregateHealth.HEALTHY
        assert report.error_count == 0
