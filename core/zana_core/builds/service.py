"""Pure lifecycle transition service with optimistic concurrency."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from zana_core.builds.models import (
    BuildLifecycleRecord,
    BuildPlan,
    CancellationAcknowledgement,
    CancellationRequest,
    Checkpoint,
    Failure,
    LifecyclePhase,
    PhaseAttempt,
    ProgressUpdate,
    RecoveryPlan,
)
from zana_core.builds.state_machine import (
    InvalidBuildTransitionError,
    is_terminal,
    require_transition,
)


class OptimisticConcurrencyError(ValueError):
    """Raised when the caller's expected revision does not match current state."""


class StaleRuntimeOrModelError(ValueError):
    """Raised when runtime disappearance or model identity drift blocks a phase."""


class BuildLifecycleService:
    """Creates immutable lifecycle revisions; history is never mutated."""

    def __init__(
        self,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._now = now

    def create_record(
        self,
        *,
        capability_digest: str,
        model_key: str,
        model_identity_digest: str | None = None,
    ) -> BuildLifecycleRecord:
        now = self._now()
        return BuildLifecycleRecord(
            record_id=f"build-{secrets.token_hex(6)}",
            capability_digest=capability_digest,
            model_key=model_key,
            model_identity_digest=model_identity_digest,
            created_at=now,
            revision=0,
            current_phase=LifecyclePhase.DRAFT,
        )

    def transition(
        self,
        record: BuildLifecycleRecord,
        *,
        expected_revision: int,
        target: LifecyclePhase,
        message: str = "",
    ) -> BuildLifecycleRecord:
        self._check_revision(record, expected_revision)
        require_transition(record.current_phase, target)
        now = self._now()
        attempt = PhaseAttempt(
            attempt_id=f"attempt-{secrets.token_hex(6)}",
            phase=target,
            started_at=now,
            progress_0_1=0.0,
            message=message,
        )
        data = record.model_dump()
        data["revision"] = record.revision + 1
        data["current_phase"] = target
        data["attempts"] = [*record.attempts, attempt]
        return BuildLifecycleRecord(**data)

    def record_progress(
        self,
        record: BuildLifecycleRecord,
        *,
        expected_revision: int,
        attempt_id: str,
        progress_0_1: float,
        message: str = "",
    ) -> BuildLifecycleRecord:
        self._check_revision(record, expected_revision)
        if record.current_phase in (LifecyclePhase.VERIFIED, LifecyclePhase.FAILED):
            raise InvalidBuildTransitionError("Terminal records cannot record progress.")
        if not any(attempt.attempt_id == attempt_id for attempt in record.attempts):
            raise ValueError("Attempt does not exist in this record.")
        update = ProgressUpdate(
            attempt_id=attempt_id,
            phase=record.current_phase,
            progress_0_1=progress_0_1,
            message=message,
        )
        data = record.model_dump()
        data["revision"] = record.revision + 1
        data["progress_history"] = [*record.progress_history, update]
        return BuildLifecycleRecord(**data)

    def add_checkpoint(
        self,
        record: BuildLifecycleRecord,
        *,
        expected_revision: int,
        attempt_id: str,
        resumable: bool,
        description: str = "",
        data: dict[str, Any] | None = None,
    ) -> BuildLifecycleRecord:
        self._check_revision(record, expected_revision)
        if not any(attempt.attempt_id == attempt_id for attempt in record.attempts):
            raise ValueError("Attempt does not exist in this record.")
        checkpoint = Checkpoint(
            checkpoint_id=f"ckpt-{secrets.token_hex(6)}",
            phase=record.current_phase,
            attempt_id=attempt_id,
            resumable=resumable,
            description=description,
            data=data or {},
        )
        data = record.model_dump()
        data["revision"] = record.revision + 1
        data["checkpoints"] = [*record.checkpoints, checkpoint]
        return BuildLifecycleRecord(**data)

    def fail(
        self,
        record: BuildLifecycleRecord,
        *,
        expected_revision: int,
        phase: LifecyclePhase,
        code: str,
        message: str,
        recoverable: bool = False,
        actions: list[str] | None = None,
    ) -> BuildLifecycleRecord:
        self._check_revision(record, expected_revision)
        failure = Failure(
            code=code,
            message=message,
            recoverable=recoverable,
            partial_artifacts_unusable=True,
            actions=actions or [],
            phase=phase,
        )
        data = record.model_dump()
        data["revision"] = record.revision + 1
        data["failures"] = [*record.failures, failure]
        return BuildLifecycleRecord(**data)

    def request_cancellation(
        self,
        record: BuildLifecycleRecord,
        *,
        expected_revision: int,
        reason: str = "",
    ) -> BuildLifecycleRecord:
        self._check_revision(record, expected_revision)
        if is_terminal(record.current_phase):
            raise InvalidBuildTransitionError("Terminal records cannot be cancelled.")
        request = CancellationRequest(
            request_id=f"cancel-{secrets.token_hex(6)}",
            phase=record.current_phase,
            reason=reason,
            requested_at=self._now(),
        )
        data = record.model_dump()
        data["revision"] = record.revision + 1
        data["cancellation_requests"] = [*record.cancellation_requests, request]
        return BuildLifecycleRecord(**data)

    def acknowledge_cancellation(
        self,
        record: BuildLifecycleRecord,
        *,
        expected_revision: int,
        request_id: str,
        acknowledgement: CancellationAcknowledgement,
    ) -> BuildLifecycleRecord:
        self._check_revision(record, expected_revision)
        if not any(request.request_id == request_id for request in record.cancellation_requests):
            raise ValueError("Cancellation request does not exist in this record.")
        data = record.model_dump()
        data["revision"] = record.revision + 1
        data["acknowledgements"] = [*record.acknowledgements, acknowledgement]
        return BuildLifecycleRecord(**data)

    def recovery_plan(self, record: BuildLifecycleRecord) -> RecoveryPlan:
        if record.current_phase != LifecyclePhase.BLOCKED:
            return RecoveryPlan(retry_allowed=False, reason="Only blocked builds can recover.")
        resumable = [
            checkpoint.checkpoint_id for checkpoint in record.checkpoints if checkpoint.resumable
        ]
        return RecoveryPlan(
            retry_allowed=True,
            resume_checkpoints=resumable,
            actions=["create_new_attempt", "revalidate_inputs"],
            reason="Explicit resume checkpoints exist; retry creates a new attempt.",
        )

    def block_for_stale_runtime(
        self,
        record: BuildLifecycleRecord,
        *,
        expected_revision: int,
        reason: str,
    ) -> BuildLifecycleRecord:
        self._check_revision(record, expected_revision)
        raise StaleRuntimeOrModelError(reason)

    def replace_plan(
        self,
        record: BuildLifecycleRecord,
        *,
        expected_revision: int,
        plan: BuildPlan,
    ) -> BuildLifecycleRecord:
        self._check_revision(record, expected_revision)
        data = record.model_dump()
        data["revision"] = record.revision + 1
        data["plan"] = plan
        return BuildLifecycleRecord(**data)

    def _check_revision(
        self,
        record: BuildLifecycleRecord,
        expected_revision: int,
    ) -> None:
        if record.revision != expected_revision:
            raise OptimisticConcurrencyError(
                f"Expected revision {expected_revision}, current revision {record.revision}."
            )
