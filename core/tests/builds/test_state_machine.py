"""Exact transition graph tests."""

from __future__ import annotations

import pytest

from zana_core.builds.models import LifecyclePhase
from zana_core.builds.state_machine import (
    InvalidBuildTransitionError,
    can_transition,
    is_active,
    is_terminal,
    require_transition,
)

VALID_PATH = [
    LifecyclePhase.DRAFT,
    LifecyclePhase.ANALYZING,
    LifecyclePhase.BASELINE_RUNNING,
    LifecyclePhase.PLANNED,
    LifecyclePhase.ACQUIRING_APPROVED_ARTIFACTS,
    LifecyclePhase.BUILDING_KNOWLEDGE,
    LifecyclePhase.TRAINING_ADAPTER,
    LifecyclePhase.MATERIALIZING,
    LifecyclePhase.EVALUATING,
    LifecyclePhase.PACKING,
    LifecyclePhase.VERIFIED,
]


class TestTransitionGraph:
    def test_every_valid_step_is_allowed(self) -> None:
        for current, target in zip(VALID_PATH, VALID_PATH[1:], strict=False):
            assert can_transition(current, target)
            require_transition(current, target)

    def test_skipped_phases_are_rejected(self) -> None:
        assert not can_transition(LifecyclePhase.DRAFT, LifecyclePhase.PLANNED)
        assert not can_transition(LifecyclePhase.ANALYZING, LifecyclePhase.PLANNED)
        assert not can_transition(LifecyclePhase.BUILDING_KNOWLEDGE, LifecyclePhase.EVALUATING)
        with pytest.raises(InvalidBuildTransitionError):
            require_transition(LifecyclePhase.DRAFT, LifecyclePhase.VERIFIED)

    def test_optional_training_can_be_skipped(self) -> None:
        assert can_transition(
            LifecyclePhase.BUILDING_KNOWLEDGE,
            LifecyclePhase.MATERIALIZING,
        )
        assert can_transition(
            LifecyclePhase.ACQUIRING_APPROVED_ARTIFACTS,
            LifecyclePhase.TRAINING_ADAPTER,
        )

    def test_terminal_states_are_immutable(self) -> None:
        for terminal in (
            LifecyclePhase.VERIFIED,
            LifecyclePhase.BLOCKED,
            LifecyclePhase.FAILED,
            LifecyclePhase.CANCELLED,
            LifecyclePhase.VERIFICATION_FAILED,
        ):
            assert is_terminal(terminal)
            assert all(
                not can_transition(terminal, target)
                for target in LifecyclePhase
                if target != terminal
            )

    def test_verification_failure_never_promotes(self) -> None:
        assert can_transition(LifecyclePhase.EVALUATING, LifecyclePhase.VERIFICATION_FAILED)
        assert not can_transition(LifecyclePhase.VERIFICATION_FAILED, LifecyclePhase.VERIFIED)
        assert not can_transition(LifecyclePhase.VERIFICATION_FAILED, LifecyclePhase.PACKING)

    def test_cancel_from_every_active_phase(self) -> None:
        active = [
            LifecyclePhase.DRAFT,
            LifecyclePhase.ANALYZING,
            LifecyclePhase.BASELINE_RUNNING,
            LifecyclePhase.PLANNED,
            LifecyclePhase.ACQUIRING_APPROVED_ARTIFACTS,
            LifecyclePhase.BUILDING_KNOWLEDGE,
            LifecyclePhase.TRAINING_ADAPTER,
            LifecyclePhase.MATERIALIZING,
            LifecyclePhase.EVALUATING,
            LifecyclePhase.PACKING,
        ]
        for phase in active:
            assert is_active(phase)
            assert can_transition(phase, LifecyclePhase.CANCELLED)
