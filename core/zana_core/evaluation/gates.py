"""Fail-closed verification gate engine."""

from __future__ import annotations

from zana_core.evaluation.models import (
    AggregateMetrics,
    GateDecision,
    GateResult,
    VerificationStatus,
)


class VerificationGateEngine:
    """Evaluates declared gates and returns an immutable status.

    A capability is verified only when every declared gate passes.
    """

    def __init__(self) -> None:
        self._absolute: list[tuple[str, float]] = []
        self._improvement: list[tuple[str, float]] = []
        self._max_drop: list[tuple[str, float]] = []

    def absolute(self, name: str, minimum: float) -> VerificationGateEngine:
        self._absolute.append((name, minimum))
        return self

    def improvement(self, name: str, minimum: float) -> VerificationGateEngine:
        self._improvement.append((name, minimum))
        return self

    def max_drop(self, name: str, maximum: float) -> VerificationGateEngine:
        self._max_drop.append((name, maximum))
        return self

    def evaluate(
        self,
        baseline: AggregateMetrics,
        candidate: AggregateMetrics,
    ) -> tuple[list[GateResult], VerificationStatus]:
        if baseline.invalid or candidate.invalid:
            return (
                [
                    GateResult(
                        name="invalid_measurements",
                        decision=GateDecision.FAIL,
                        observed=0.0,
                        message="Invalid baseline or candidate measurements cannot be verified.",
                    )
                ],
                VerificationStatus.VERIFICATION_FAILED,
            )
        if baseline.cases == 0 or candidate.cases == 0:
            return (
                [
                    GateResult(
                        name="empty_measurements",
                        decision=GateDecision.FAIL,
                        observed=0.0,
                        message="An empty suite cannot be verified.",
                    )
                ],
                VerificationStatus.VERIFICATION_FAILED,
            )

        results: list[GateResult] = []
        passed = True
        for name, minimum in self._absolute:
            result = self._absolute_gate(name, candidate.pass_rate, minimum)
            passed = passed and result.decision == GateDecision.PASS
            results.append(result)
        for name, minimum in self._improvement:
            result = self._improvement_gate(name, candidate.pass_rate, baseline.pass_rate, minimum)
            passed = passed and result.decision == GateDecision.PASS
            results.append(result)
        for name, maximum in self._max_drop:
            result = self._max_drop_gate(name, candidate.pass_rate, baseline.pass_rate, maximum)
            passed = passed and result.decision == GateDecision.PASS
            results.append(result)

        if not results:
            results.append(
                GateResult(
                    name="no_gates",
                    decision=GateDecision.FAIL,
                    observed=candidate.pass_rate,
                    message="No verification gates were declared; verification fails closed.",
                )
            )
            passed = False
        return (
            results,
            VerificationStatus.VERIFIED_LOCAL if passed else VerificationStatus.VERIFICATION_FAILED,
        )

    @staticmethod
    def _absolute_gate(name: str, observed: float, minimum: float) -> GateResult:
        passed = observed >= minimum
        return GateResult(
            name=name,
            decision=GateDecision.PASS if passed else GateDecision.FAIL,
            observed=observed,
            threshold=minimum,
            message=(
                f"absolute gate {name} passed ({observed:.3f} >= {minimum:.3f})"
                if passed
                else f"absolute gate {name} failed ({observed:.3f} < {minimum:.3f})"
            ),
        )

    @staticmethod
    def _improvement_gate(
        name: str,
        candidate: float,
        baseline: float,
        minimum: float,
    ) -> GateResult:
        delta = candidate - baseline
        passed = delta >= minimum
        return GateResult(
            name=name,
            decision=GateDecision.PASS if passed else GateDecision.FAIL,
            observed=round(delta, 6),
            threshold=minimum,
            message=(
                f"improvement gate {name} passed (delta {delta:.3f} >= {minimum:.3f})"
                if passed
                else f"improvement gate {name} failed (delta {delta:.3f} < {minimum:.3f})"
            ),
        )

    @staticmethod
    def _max_drop_gate(
        name: str,
        candidate: float,
        baseline: float,
        maximum: float,
    ) -> GateResult:
        delta = candidate - baseline
        passed = delta >= -maximum
        return GateResult(
            name=name,
            decision=GateDecision.PASS if passed else GateDecision.FAIL,
            observed=round(delta, 6),
            threshold=-maximum,
            message=(
                f"regression gate {name} passed (delta {delta:.3f} >= -{maximum:.3f})"
                if passed
                else f"regression gate {name} failed (delta {delta:.3f} < -{maximum:.3f})"
            ),
        )
