"""Valid and cancellation-safe job state transitions."""

from __future__ import annotations

from zana_core.domain.enums import BuildJobStatus, JobStatus

BUILD_JOB_TRANSITIONS: dict[BuildJobStatus, frozenset[BuildJobStatus]] = {
    BuildJobStatus.DRAFT: frozenset(
        {
            BuildJobStatus.ANALYZING,
            BuildJobStatus.BLOCKED,
            BuildJobStatus.FAILED,
            BuildJobStatus.CANCELLED,
        }
    ),
    BuildJobStatus.ANALYZING: frozenset(
        {
            BuildJobStatus.BASELINE_RUNNING,
            BuildJobStatus.BLOCKED,
            BuildJobStatus.FAILED,
            BuildJobStatus.CANCELLED,
        }
    ),
    BuildJobStatus.BASELINE_RUNNING: frozenset(
        {
            BuildJobStatus.PLANNED,
            BuildJobStatus.BLOCKED,
            BuildJobStatus.FAILED,
            BuildJobStatus.CANCELLED,
        }
    ),
    BuildJobStatus.PLANNED: frozenset(
        {
            BuildJobStatus.ACQUIRING_APPROVED_ARTIFACTS,
            BuildJobStatus.BLOCKED,
            BuildJobStatus.FAILED,
            BuildJobStatus.CANCELLED,
        }
    ),
    BuildJobStatus.ACQUIRING_APPROVED_ARTIFACTS: frozenset(
        {
            BuildJobStatus.BUILDING_KNOWLEDGE,
            BuildJobStatus.TRAINING_ADAPTER,
            BuildJobStatus.BLOCKED,
            BuildJobStatus.FAILED,
            BuildJobStatus.CANCELLED,
        }
    ),
    BuildJobStatus.BUILDING_KNOWLEDGE: frozenset(
        {
            BuildJobStatus.TRAINING_ADAPTER,
            BuildJobStatus.MATERIALIZING,
            BuildJobStatus.BLOCKED,
            BuildJobStatus.FAILED,
            BuildJobStatus.CANCELLED,
        }
    ),
    BuildJobStatus.TRAINING_ADAPTER: frozenset(
        {
            BuildJobStatus.MATERIALIZING,
            BuildJobStatus.BLOCKED,
            BuildJobStatus.FAILED,
            BuildJobStatus.CANCELLED,
        }
    ),
    BuildJobStatus.MATERIALIZING: frozenset(
        {
            BuildJobStatus.EVALUATING,
            BuildJobStatus.BLOCKED,
            BuildJobStatus.FAILED,
            BuildJobStatus.CANCELLED,
        }
    ),
    BuildJobStatus.EVALUATING: frozenset(
        {
            BuildJobStatus.PACKING,
            BuildJobStatus.VERIFICATION_FAILED,
            BuildJobStatus.BLOCKED,
            BuildJobStatus.FAILED,
            BuildJobStatus.CANCELLED,
        }
    ),
    BuildJobStatus.PACKING: frozenset(
        {
            BuildJobStatus.VERIFIED,
            BuildJobStatus.BLOCKED,
            BuildJobStatus.FAILED,
            BuildJobStatus.CANCELLED,
        }
    ),
    BuildJobStatus.VERIFIED: frozenset(),
    BuildJobStatus.BLOCKED: frozenset(),
    BuildJobStatus.FAILED: frozenset(),
    BuildJobStatus.CANCELLED: frozenset(),
    BuildJobStatus.VERIFICATION_FAILED: frozenset(),
}

GENERIC_JOB_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.PENDING: frozenset({JobStatus.RUNNING, JobStatus.FAILED, JobStatus.CANCELLED}),
    JobStatus.RUNNING: frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}),
    JobStatus.SUCCEEDED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}

TERMINAL_BUILD_STATES = frozenset(
    {
        BuildJobStatus.VERIFIED,
        BuildJobStatus.BLOCKED,
        BuildJobStatus.FAILED,
        BuildJobStatus.CANCELLED,
        BuildJobStatus.VERIFICATION_FAILED,
    }
)

TERMINAL_JOB_STATES = frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED})


class InvalidJobTransitionError(ValueError):
    """Raised when a transition is not allowed by the lifecycle."""


def can_transition_build(current: BuildJobStatus, target: BuildJobStatus) -> bool:
    return target in BUILD_JOB_TRANSITIONS[current]


def require_transition_build(current: BuildJobStatus, target: BuildJobStatus) -> None:
    if not can_transition_build(current, target):
        raise InvalidJobTransitionError(
            f"Build job transition {current.value} -> {target.value} is not allowed."
        )


def can_transition_job(current: JobStatus, target: JobStatus) -> bool:
    return target in GENERIC_JOB_TRANSITIONS[current]


def require_transition_job(current: JobStatus, target: JobStatus) -> None:
    if not can_transition_job(current, target):
        raise InvalidJobTransitionError(
            f"Job transition {current.value} -> {target.value} is not allowed."
        )
