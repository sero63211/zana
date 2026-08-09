"""Deterministic aggregation of raw scorer results."""

from __future__ import annotations

from zana_core.evaluation.models import AggregateMetrics, ScorerResult


def aggregate(results: list[ScorerResult]) -> AggregateMetrics:
    """Aggregate raw results, handling empty and invalid inputs explicitly."""
    cases = len(results)
    if cases == 0:
        return AggregateMetrics(cases=0, passed=0, failed=0, pass_rate=0.0, invalid=0)
    passed = sum(1 for result in results if result.passed)
    invalid = sum(
        1 for result in results if result.score not in (0.0, 1.0) or not 0 <= result.score <= 1
    )
    failed = cases - passed - invalid
    valid_count = cases - invalid
    pass_rate = passed / valid_count if valid_count else 0.0
    return AggregateMetrics(
        cases=cases,
        passed=passed,
        failed=failed,
        pass_rate=round(pass_rate, 6),
        invalid=invalid,
    )
