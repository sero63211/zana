"""Instance snapshot, update, and rollback orchestration protocols."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from enum import Enum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from zana_core.memory.models import (
    ImagePointer,
    InstancePointer,
    InstanceSnapshot,
    MutableInstanceState,
)


class UpdateCheckKind(str, Enum):
    """Canonical pre-switch checks, always run in this order."""

    COMPATIBILITY = "compatibility"
    MIGRATION = "migration"
    SMOKE = "smoke"


UPDATE_CHECK_ORDER: tuple[UpdateCheckKind, ...] = (
    UpdateCheckKind.COMPATIBILITY,
    UpdateCheckKind.MIGRATION,
    UpdateCheckKind.SMOKE,
)


class CheckOutcome(BaseModel):
    """Result of one compatibility, migration, or smoke check."""

    model_config = ConfigDict(extra="forbid")

    kind: UpdateCheckKind
    ok: bool
    message: str = ""


class UpdateCheck(Protocol):
    """A hook invoked before the atomic image-pointer switch."""

    kind: UpdateCheckKind

    def run(self, state: MutableInstanceState, candidate: ImagePointer) -> CheckOutcome: ...


class InstanceUpdateResult(BaseModel):
    """Outcome of an attempted image update."""

    model_config = ConfigDict(extra="forbid")

    applied: bool
    image_before: ImagePointer
    image_after: ImagePointer
    outcomes: list[CheckOutcome] = Field(default_factory=list)
    blocked_at: UpdateCheckKind | None = None
    retained_snapshot: InstanceSnapshot | None = None
    rollback_available: bool = False
    external_side_effects_not_reverted: bool = True
    message: str


class InstanceRollbackResult(BaseModel):
    """Outcome of a rollback to a retained snapshot."""

    model_config = ConfigDict(extra="forbid")

    rolled_back: bool
    image_before: ImagePointer
    image_after: ImagePointer
    restored_snapshot: InstanceSnapshot
    snapshot_count: int
    external_side_effects_not_reverted: bool = True
    message: str


class InstanceError(Exception):
    """Base error for instance update orchestration."""


class RollbackTargetMismatchError(InstanceError):
    """The snapshot belongs to a different instance."""


class SnapshotNotRetainedError(InstanceError):
    """Rollback may only target a snapshot retained by this orchestrator."""


class InstanceUpdateOrchestrator:
    """Atomic image switch after compatibility, migration, and smoke checks.

    Snapshots are captured on every successful update and are never deleted by
    later updates or rollbacks. Rollback restores the prior image pointer and
    mutable snapshot but never claims to undo external side effects.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._snapshots: dict[str, InstanceSnapshot] = {}
        self._snapshot_counter = 0

    def snapshots(self) -> list[InstanceSnapshot]:
        """Retained snapshots in deterministic capture order."""
        return sorted(
            self._snapshots.values(),
            key=lambda snapshot: (snapshot.captured_at, snapshot.snapshot_id),
        )

    def update(
        self,
        instance: InstancePointer,
        state: MutableInstanceState,
        candidate: ImagePointer,
        checks: Sequence[UpdateCheck],
    ) -> InstanceUpdateResult:
        """Switch the image pointer only after all required checks pass."""
        kind_map: dict[UpdateCheckKind, UpdateCheck] = {}
        for check in checks:
            if check.kind in kind_map:
                raise ValueError(f"duplicate check hook for {check.kind.value}")
            kind_map[check.kind] = check

        outcomes: list[CheckOutcome] = []
        blocked: UpdateCheckKind | None = None
        message = ""
        for kind in UPDATE_CHECK_ORDER:
            hook = kind_map.get(kind)
            if hook is None:
                blocked = kind
                message = f"missing {kind.value} check hook"
                break
            outcome = hook.run(state, candidate)
            outcomes.append(outcome)
            if not outcome.ok:
                blocked = kind
                message = f"update blocked at {kind.value} check: {outcome.message}"
                break

        if blocked is not None:
            return InstanceUpdateResult(
                applied=False,
                image_before=instance.image,
                image_after=instance.image,
                outcomes=outcomes,
                blocked_at=blocked,
                retained_snapshot=None,
                rollback_available=False,
                external_side_effects_not_reverted=True,
                message=message,
            )

        snapshot = self._capture(instance, state, reason="pre_update")
        instance.image = candidate
        instance.snapshot_revision += 1
        instance.updated_at = self._clock()
        state.state_revision += 1
        state.updated_at = self._clock()
        return InstanceUpdateResult(
            applied=True,
            image_before=snapshot.image,
            image_after=candidate,
            outcomes=outcomes,
            blocked_at=None,
            retained_snapshot=snapshot,
            rollback_available=True,
            external_side_effects_not_reverted=True,
            message="update applied atomically after all checks passed",
        )

    def rollback(
        self,
        instance: InstancePointer,
        state: MutableInstanceState,
        snapshot: InstanceSnapshot,
    ) -> InstanceRollbackResult:
        """Restore a retained snapshot; external side effects are not undone."""
        if snapshot.instance_id != instance.instance_id:
            raise RollbackTargetMismatchError(
                f"snapshot {snapshot.snapshot_id} belongs to instance "
                f"{snapshot.instance_id}, not {instance.instance_id}"
            )
        if snapshot.snapshot_id not in self._snapshots:
            raise SnapshotNotRetainedError(
                f"snapshot {snapshot.snapshot_id} is not retained by this orchestrator"
            )

        image_before = instance.image
        restored = snapshot.state.model_copy(deep=True)
        restored.state_revision = state.state_revision + 1
        restored.updated_at = self._clock()

        instance.image = snapshot.image
        instance.snapshot_revision += 1
        instance.updated_at = restored.updated_at
        state.conversation = list(restored.conversation)
        state.approved_facts = list(restored.approved_facts)
        state.approved_preferences = list(restored.approved_preferences)
        state.state_revision = restored.state_revision
        state.updated_at = restored.updated_at

        return InstanceRollbackResult(
            rolled_back=True,
            image_before=image_before,
            image_after=snapshot.image,
            restored_snapshot=snapshot,
            snapshot_count=len(self._snapshots),
            external_side_effects_not_reverted=True,
            message=(
                "rollback restored the prior image pointer and mutable snapshot; "
                "external side effects cannot be reverted"
            ),
        )

    def _capture(
        self,
        instance: InstancePointer,
        state: MutableInstanceState,
        *,
        reason: str,
    ) -> InstanceSnapshot:
        self._snapshot_counter += 1
        snapshot = InstanceSnapshot(
            snapshot_id=f"snap-{instance.instance_id}-{self._snapshot_counter}",
            instance_id=instance.instance_id,
            image=instance.image,
            state=state.model_copy(deep=True),
            captured_at=self._clock(),
            reason=reason,
        )
        self._snapshots[snapshot.snapshot_id] = snapshot
        return snapshot
