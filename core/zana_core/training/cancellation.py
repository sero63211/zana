"""Cancellation and partial-artifact state machine contracts."""

from __future__ import annotations

from zana_core.training.contracts import AdapterState, CancellationState, RunRecord

ALLOWED_TRANSITIONS: dict[CancellationState, frozenset[CancellationState]] = {
    CancellationState.RUNNING: frozenset(
        {CancellationState.CANCELLING, CancellationState.FAILED, CancellationState.COMPLETED}
    ),
    CancellationState.CANCELLING: frozenset(
        {CancellationState.CANCELLED, CancellationState.FAILED}
    ),
    CancellationState.CANCELLED: frozenset(),
    CancellationState.FAILED: frozenset(),
    CancellationState.COMPLETED: frozenset(),
}


class InvalidCancellationTransitionError(ValueError):
    """Raised for an illegal cancellation/artifact transition."""


def transition_run(run: RunRecord, target: CancellationState) -> RunRecord:
    """Return a new run record after validating the transition."""
    allowed = ALLOWED_TRANSITIONS[run.state]
    if target not in allowed:
        raise InvalidCancellationTransitionError(
            f"cannot transition {run.state.value} -> {target.value}"
        )
    updated = run.model_copy(update={"state": target})
    return updated


def mark_cancelled(run: RunRecord, *, log_path=None) -> RunRecord:
    """Cancel a run, retain logs, and mark partial outputs unusable."""
    return transition_run(run, CancellationState.CANCELLING).model_copy(
        update={
            "state": CancellationState.CANCELLED,
            "log_path": log_path or run.log_path,
            "adapter": None,
            "partial_outputs": tuple(run.partial_outputs),
        }
    )


def promote_adapter(run: RunRecord, adapter_state: AdapterState) -> RunRecord:
    """Never promote a partial adapter; completed runs may promote complete ones."""
    if run.state != CancellationState.COMPLETED:
        raise InvalidCancellationTransitionError("only a completed run may promote an adapter")
    if adapter_state != AdapterState.COMPLETE:
        raise InvalidCancellationTransitionError("partial adapters cannot be promoted")
    return run
