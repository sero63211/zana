"""Exact fail-closed build transition graph."""

from __future__ import annotations

from zana_core.builds.models import LifecyclePhase

ACTIVE_PHASES = frozenset(
    {
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
    }
)

TERMINAL_PHASES = frozenset(
    {
        LifecyclePhase.VERIFIED,
        LifecyclePhase.BLOCKED,
        LifecyclePhase.FAILED,
        LifecyclePhase.CANCELLED,
        LifecyclePhase.VERIFICATION_FAILED,
    }
)

TRANSITIONS: dict[LifecyclePhase, frozenset[LifecyclePhase]] = {
    LifecyclePhase.DRAFT: frozenset(
        {
            LifecyclePhase.ANALYZING,
            LifecyclePhase.BLOCKED,
            LifecyclePhase.FAILED,
            LifecyclePhase.CANCELLED,
        }
    ),
    LifecyclePhase.ANALYZING: frozenset(
        {
            LifecyclePhase.BASELINE_RUNNING,
            LifecyclePhase.BLOCKED,
            LifecyclePhase.FAILED,
            LifecyclePhase.CANCELLED,
        }
    ),
    LifecyclePhase.BASELINE_RUNNING: frozenset(
        {
            LifecyclePhase.PLANNED,
            LifecyclePhase.BLOCKED,
            LifecyclePhase.FAILED,
            LifecyclePhase.CANCELLED,
        }
    ),
    LifecyclePhase.PLANNED: frozenset(
        {
            LifecyclePhase.ACQUIRING_APPROVED_ARTIFACTS,
            LifecyclePhase.BLOCKED,
            LifecyclePhase.FAILED,
            LifecyclePhase.CANCELLED,
        }
    ),
    LifecyclePhase.ACQUIRING_APPROVED_ARTIFACTS: frozenset(
        {
            LifecyclePhase.BUILDING_KNOWLEDGE,
            LifecyclePhase.TRAINING_ADAPTER,
            LifecyclePhase.BLOCKED,
            LifecyclePhase.FAILED,
            LifecyclePhase.CANCELLED,
        }
    ),
    LifecyclePhase.BUILDING_KNOWLEDGE: frozenset(
        {
            LifecyclePhase.TRAINING_ADAPTER,
            LifecyclePhase.MATERIALIZING,
            LifecyclePhase.BLOCKED,
            LifecyclePhase.FAILED,
            LifecyclePhase.CANCELLED,
        }
    ),
    LifecyclePhase.TRAINING_ADAPTER: frozenset(
        {
            LifecyclePhase.MATERIALIZING,
            LifecyclePhase.BLOCKED,
            LifecyclePhase.FAILED,
            LifecyclePhase.CANCELLED,
        }
    ),
    LifecyclePhase.MATERIALIZING: frozenset(
        {
            LifecyclePhase.EVALUATING,
            LifecyclePhase.BLOCKED,
            LifecyclePhase.FAILED,
            LifecyclePhase.CANCELLED,
        }
    ),
    LifecyclePhase.EVALUATING: frozenset(
        {
            LifecyclePhase.PACKING,
            LifecyclePhase.VERIFICATION_FAILED,
            LifecyclePhase.BLOCKED,
            LifecyclePhase.FAILED,
            LifecyclePhase.CANCELLED,
        }
    ),
    LifecyclePhase.PACKING: frozenset(
        {
            LifecyclePhase.VERIFIED,
            LifecyclePhase.BLOCKED,
            LifecyclePhase.FAILED,
            LifecyclePhase.CANCELLED,
        }
    ),
    LifecyclePhase.VERIFIED: frozenset(),
    LifecyclePhase.BLOCKED: frozenset(),
    LifecyclePhase.FAILED: frozenset(),
    LifecyclePhase.CANCELLED: frozenset(),
    LifecyclePhase.VERIFICATION_FAILED: frozenset(),
}


class InvalidBuildTransitionError(ValueError):
    """Raised when a transition violates the exact lifecycle graph."""


def can_transition(current: LifecyclePhase, target: LifecyclePhase) -> bool:
    return target in TRANSITIONS[current]


def require_transition(current: LifecyclePhase, target: LifecyclePhase) -> None:
    if not can_transition(current, target):
        raise InvalidBuildTransitionError(
            f"Build transition {current.value} -> {target.value} is not allowed."
        )


def is_active(phase: LifecyclePhase) -> bool:
    return phase in ACTIVE_PHASES


def is_terminal(phase: LifecyclePhase) -> bool:
    return phase in TERMINAL_PHASES
