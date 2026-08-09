"""Held-out identifier isolation tests."""

from __future__ import annotations

from zana_core.evaluation.heldout import check_held_out_isolation
from zana_core.evaluation.models import EvaluationCase, ScorerConfig, ScorerType


def _case(case_id: str) -> EvaluationCase:
    return EvaluationCase(
        id=case_id,
        prompt="prompt",
        scorer=ScorerConfig(type=ScorerType.EXACT_STRING, expected="x"),
    )


class TestHeldOutIsolation:
    def test_disjoint_ids_are_isolated(self) -> None:
        report = check_held_out_isolation(
            [_case("eval-001"), _case("eval-002")],
            ["train-001", "train-002"],
        )
        assert report.ok is True
        assert report.duplicate_ids == ()

    def test_shared_id_is_detected(self) -> None:
        report = check_held_out_isolation(
            [_case("eval-001"), _case("train-001")],
            ["train-001"],
        )
        assert report.ok is False
        assert report.duplicate_ids == ("train-001",)

    def test_empty_sets_are_isolated(self) -> None:
        assert check_held_out_isolation([], []).ok is True
