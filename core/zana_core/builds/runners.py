"""Pure phase-runner/progress/event protocols for later integration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from zana_core.builds.models import (
    CancellationAcknowledgement,
    CancellationRequest,
    Checkpoint,
    CleanupPlan,
    PhaseAttempt,
    ProgressUpdate,
)


class PhaseRunner(Protocol):
    """Executable phase runner boundary; this lane never executes runners."""

    def run(self, attempt: PhaseAttempt) -> list[ProgressUpdate]: ...


class ProgressSink(Protocol):
    """Records truthful progress updates emitted by a runner."""

    def emit(self, update: ProgressUpdate) -> None: ...


class CancellationObserver(Protocol):
    """Observes cancellation requests and returns explicit acknowledgements."""

    def acknowledge(
        self,
        request: CancellationRequest,
        cleanup_plan: CleanupPlan,
    ) -> CancellationAcknowledgement: ...


class CheckpointObserver(Protocol):
    """Observes explicit resumable checkpoints."""

    def observe(self, checkpoint: Checkpoint) -> None: ...


ProgressCallback = Callable[[ProgressUpdate], None]
