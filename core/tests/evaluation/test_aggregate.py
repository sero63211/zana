"""Deterministic aggregation math including empty and invalid inputs."""

from __future__ import annotations

from zana_core.evaluation.aggregate import aggregate
from zana_core.evaluation.models import ScorerResult, ScorerType


def _result(passed: bool, *, score: float | None = None) -> ScorerResult:
    return ScorerResult(
        case_id="case",
        scorer_type=ScorerType.EXACT_STRING,
        passed=passed,
        score=1.0 if passed else 0.0 if score is None else score,
    )


class TestAggregate:
    def test_empty_results_are_explicit(self) -> None:
        metrics = aggregate([])
        assert metrics.cases == 0
        assert metrics.passed == 0
        assert metrics.failed == 0
        assert metrics.pass_rate == 0.0
        assert metrics.invalid == 0

    def test_all_passed(self) -> None:
        metrics = aggregate([_result(True), _result(True), _result(True)])
        assert metrics.cases == 3
        assert metrics.passed == 3
        assert metrics.pass_rate == 1.0

    def test_mixed_results(self) -> None:
        metrics = aggregate([_result(True), _result(False), _result(True), _result(False)])
        assert metrics.passed == 2
        assert metrics.failed == 2
        assert metrics.pass_rate == 0.5

    def test_invalid_score_is_counted_separately(self) -> None:
        metrics = aggregate([_result(True), _result(False, score=0.5), _result(False)])
        assert metrics.invalid == 1
        assert metrics.passed == 1
        assert metrics.failed == 1
        assert metrics.pass_rate == 0.5
