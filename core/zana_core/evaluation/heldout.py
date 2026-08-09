"""Held-out identifier isolation checks for evaluation records."""

from __future__ import annotations

from dataclasses import dataclass

from zana_core.evaluation.models import EvaluationCase


@dataclass(frozen=True, slots=True)
class HeldOutIsolation:
    """Report whether evaluation ids stay disjoint from training identifiers."""

    duplicate_ids: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.duplicate_ids


def check_held_out_isolation(
    evaluation_cases: list[EvaluationCase],
    training_ids: list[str],
) -> HeldOutIsolation:
    """Reject any evaluation case id that also appears in training identifiers."""
    training = set(training_ids)
    duplicates = sorted({case.id for case in evaluation_cases} & training)
    return HeldOutIsolation(duplicate_ids=tuple(duplicates))
