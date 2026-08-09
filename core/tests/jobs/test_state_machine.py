"""Build and generic job transition validation tests."""

from __future__ import annotations

import pytest

from zana_core.domain.enums import BuildJobStatus, JobStatus
from zana_core.jobs.state_machine import (
    InvalidJobTransitionError,
    can_transition_build,
    can_transition_job,
    require_transition_build,
    require_transition_job,
)


class TestBuildStateMachine:
    def test_valid_path_reaches_verified(self) -> None:
        path = [
            BuildJobStatus.DRAFT,
            BuildJobStatus.ANALYZING,
            BuildJobStatus.BASELINE_RUNNING,
            BuildJobStatus.PLANNED,
            BuildJobStatus.ACQUIRING_APPROVED_ARTIFACTS,
            BuildJobStatus.BUILDING_KNOWLEDGE,
            BuildJobStatus.TRAINING_ADAPTER,
            BuildJobStatus.MATERIALIZING,
            BuildJobStatus.EVALUATING,
            BuildJobStatus.PACKING,
            BuildJobStatus.VERIFIED,
        ]
        for current, target in zip(path, path[1:], strict=False):
            assert can_transition_build(current, target)
            require_transition_build(current, target)

    def test_skipped_states_are_rejected(self) -> None:
        assert not can_transition_build(BuildJobStatus.DRAFT, BuildJobStatus.VERIFIED)
        assert not can_transition_build(BuildJobStatus.ANALYZING, BuildJobStatus.PLANNED)
        with pytest.raises(InvalidJobTransitionError):
            require_transition_build(BuildJobStatus.VERIFIED, BuildJobStatus.CANCELLED)

    def test_cancel_is_allowed_from_every_active_state(self) -> None:
        active = [
            BuildJobStatus.DRAFT,
            BuildJobStatus.ANALYZING,
            BuildJobStatus.BASELINE_RUNNING,
            BuildJobStatus.PLANNED,
            BuildJobStatus.ACQUIRING_APPROVED_ARTIFACTS,
            BuildJobStatus.BUILDING_KNOWLEDGE,
            BuildJobStatus.TRAINING_ADAPTER,
            BuildJobStatus.MATERIALIZING,
            BuildJobStatus.EVALUATING,
            BuildJobStatus.PACKING,
        ]
        for state in active:
            assert can_transition_build(state, BuildJobStatus.CANCELLED)


class TestGenericJobStateMachine:
    def test_valid_lifecycle(self) -> None:
        assert can_transition_job(JobStatus.PENDING, JobStatus.RUNNING)
        assert can_transition_job(JobStatus.RUNNING, JobStatus.SUCCEEDED)
        assert can_transition_job(JobStatus.RUNNING, JobStatus.FAILED)
        require_transition_job(JobStatus.PENDING, JobStatus.RUNNING)

    def test_terminal_states_are_immutable(self) -> None:
        for terminal in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED):
            assert not can_transition_job(terminal, JobStatus.RUNNING)
            with pytest.raises(InvalidJobTransitionError):
                require_transition_job(terminal, JobStatus.PENDING)
