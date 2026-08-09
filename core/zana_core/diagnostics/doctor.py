"""Deterministic sequential DoctorService with bounded budgets."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence

from zana_core.diagnostics.models import (
    AggregateHealth,
    CheckStatus,
    DiagnosticCheck,
    DiagnosticIssue,
    DiagnosticReport,
    Evidence,
    ProbeBudget,
    RecoveryAction,
    Severity,
)
from zana_core.diagnostics.probes import DiagnosticProbe, ProbeTimeoutError


class BudgetExceededError(ValueError):
    """Raised when a diagnostic budget would be exceeded."""


class DoctorService:
    """Runs bounded sequential probes; no threads, daemons, or telemetry."""

    def __init__(
        self,
        *,
        budget: ProbeBudget | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.budget = budget or ProbeBudget()
        self._clock = clock

    def run(self, probes: Sequence[DiagnosticProbe]) -> DiagnosticReport:
        if len(probes) > self.budget.max_checks:
            raise BudgetExceededError(
                f"Check count {len(probes)} exceeds budget {self.budget.max_checks}."
            )
        checks: list[DiagnosticCheck] = []
        started = self._clock()
        for probe in probes:
            elapsed = self._clock() - started
            if elapsed >= self.budget.total_budget_seconds:
                checks.append(self._budget_check("total time budget exceeded"))
                break
            checks.append(self._run_one(probe, started))
        total = self._clock() - started
        return self._build_report(checks, total)

    def _run_one(self, probe: DiagnosticProbe, started: float) -> DiagnosticCheck:
        start = self._clock()
        try:
            check = probe.run(self.budget)
        except TimeoutError as error:
            raise ProbeTimeoutError("Probe timed out.") from error
        except Exception:  # noqa: BLE001
            duration = self._clock() - start
            return DiagnosticCheck(
                check_id=probe.check_id,
                name=probe.name,
                status=CheckStatus.UNAVAILABLE,
                severity=Severity.WARN,
                duration_seconds=duration,
                observed_source="doctor",
                evidence=Evidence(
                    observed_source="doctor",
                    boolean_presence=False,
                    notes=["probe failed without crashing the report"],
                ),
                issues=[
                    DiagnosticIssue(
                        code="PROBE_FAILED",
                        severity=Severity.WARN,
                        message="A diagnostic probe failed; the report remains usable.",
                        recovery_actions=[
                            RecoveryAction(
                                code="RETRY_DOCTOR",
                                message="Run the doctor again after correcting the environment.",
                            )
                        ],
                    )
                ],
            )
        return check

    def _budget_check(self, message: str) -> DiagnosticCheck:
        return DiagnosticCheck(
            check_id="budget",
            name="Diagnostic budget",
            status=CheckStatus.SKIPPED,
            severity=Severity.WARN,
            duration_seconds=0.0,
            observed_source="doctor",
            evidence=Evidence(
                observed_source="doctor",
                boolean_presence=False,
                notes=[message],
            ),
        )

    def _build_report(
        self,
        checks: list[DiagnosticCheck],
        total: float,
    ) -> DiagnosticReport:
        error_count = sum(1 for item in checks if item.status == CheckStatus.FAIL)
        skipped = sum(
            1 for item in checks if item.status in (CheckStatus.UNAVAILABLE, CheckStatus.SKIPPED)
        )
        mandatory_failures = [
            item
            for item in checks
            if item.status == CheckStatus.FAIL
            and any(issue.severity == Severity.ERROR for issue in item.issues)
        ]
        if mandatory_failures:
            health = AggregateHealth.FAILED
        elif any(item.status == CheckStatus.WARN for item in checks):
            health = AggregateHealth.PASS_WITH_LIMITED_FEATURES
        else:
            health = AggregateHealth.HEALTHY
        return DiagnosticReport(
            budget=self.budget,
            checks=checks,
            aggregate_health=health,
            total_duration_seconds=total,
            skipped_or_unavailable_count=skipped,
            error_count=error_count,
            details={"probe_count": len(checks)},
        )
