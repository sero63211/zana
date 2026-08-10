"""Injected cancellation and progress boundary for product operations.

The canonical archive services own their internal chunk loops and expose only
deadline bounds; this boundary is called at every product phase transition and
at the chunk-safe entry points the owned services permit. It never claims
interrupt capability inside the canonical pack/unpack loops.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from zana_core.portability.models import OperationStage, PortabilityError, RecoveryAction

MAX_PROGRESS_ITEMS = 64

CancelProbe = Callable[[], bool]
ProgressSink = Callable[[str, float], None]


class OperationCancelledError(PortabilityError):
    """Typed cancellation failure carrying the exact CANCELLED code."""


class OperationBoundary:
    """Fail-closed cancel probe plus bounded real-stage progress sink."""

    def __init__(
        self,
        *,
        cancel: CancelProbe | None = None,
        progress: ProgressSink | None = None,
    ) -> None:
        if cancel is not None and not callable(cancel):
            raise PortabilityError(
                "cancel probe must be callable",
                code="BOUNDARY_INVALID",
                stage=OperationStage.PREFLIGHT,
            )
        if progress is not None and not callable(progress):
            raise PortabilityError(
                "progress sink must be callable",
                code="BOUNDARY_INVALID",
                stage=OperationStage.PREFLIGHT,
            )
        self._cancel = cancel
        self._progress = progress
        self._items = 0

    def check(self, stage: OperationStage, *, fraction: float | None = None) -> None:
        """Check cancellation and report one real stage transition."""
        if type(stage) is not OperationStage:
            raise PortabilityError(
                "boundary stage must be an exact OperationStage",
                code="BOUNDARY_INVALID",
                stage=OperationStage.PREFLIGHT,
            )
        if fraction is not None:
            if (
                type(fraction) not in (int, float)
                or type(fraction) is bool
                or not math.isfinite(float(fraction))
                or not 0.0 <= float(fraction) <= 1.0
            ):
                raise PortabilityError(
                    "boundary progress must be a finite fraction in 0..1",
                    code="BOUNDARY_INVALID",
                    stage=stage,
                )
            fraction = float(fraction)
        if self._items >= MAX_PROGRESS_ITEMS:
            raise PortabilityError(
                "operation progress exceeded the hard limit",
                code="PROGRESS_LIMIT_EXCEEDED",
                stage=stage,
                recovery_action=RecoveryAction.RETRY,
            )
        cancelled = False
        if self._cancel is not None:
            try:
                result = self._cancel()
            except Exception as error:
                raise PortabilityError(
                    "cancel probe failed closed",
                    code="CANCELLATION_PROBE_FAILED",
                    stage=stage,
                ) from error
            if type(result) is not bool:
                raise PortabilityError(
                    "cancel probe returned an invalid result",
                    code="CANCELLATION_PROBE_FAILED",
                    stage=stage,
                )
            cancelled = result
        if cancelled:
            raise OperationCancelledError(
                "operation was cancelled",
                code="CANCELLED",
                stage=stage,
                recovery_action=RecoveryAction.RETRY,
            )
        self._items += 1
        if self._progress is not None:
            try:
                self._progress(stage.value, fraction if fraction is not None else 0.0)
            except Exception as error:
                raise PortabilityError(
                    "progress sink failed closed",
                    code="PROGRESS_SINK_FAILED",
                    stage=stage,
                ) from error

    def notify_complete(self, stage: OperationStage, *, fraction: float | None = None) -> None:
        """Non-failing post-commit notification; never cancels and never raises."""
        if type(stage) is not OperationStage:
            return
        if self._progress is None:
            return
        try:
            self._progress(stage.value, fraction if fraction is not None else 1.0)
        except Exception:
            return

    @staticmethod
    def noop() -> OperationBoundary:
        return OperationBoundary()
