"""Tests for instance snapshot, update, and rollback orchestration."""

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from zana_core.memory.instance import (
    CheckOutcome,
    InstanceRollbackResult,
    InstanceUpdateOrchestrator,
    InstanceUpdateResult,
    RollbackTargetMismatchError,
    SnapshotNotRetainedError,
    UpdateCheck,
    UpdateCheckKind,
)
from zana_core.memory.models import (
    ImagePointer,
    InstancePointer,
    MutableInstanceState,
)

FIXED = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


@dataclass
class FakeCheck:
    """Check hook with a canned outcome."""

    kind: UpdateCheckKind
    ok: bool
    message: str = ""

    def run(self, state: MutableInstanceState, candidate: ImagePointer) -> CheckOutcome:
        return CheckOutcome(kind=self.kind, ok=self.ok, message=self.message)


def image(digest: str) -> ImagePointer:
    return ImagePointer(digest=digest)


def pointer() -> InstancePointer:
    return InstancePointer(
        instance_id="inst-1",
        image=image("sha256:image-v1"),
        snapshot_revision=1,
        state_schema_version=1,
        updated_at=FIXED,
    )


def state() -> MutableInstanceState:
    return MutableInstanceState(
        instance_id="inst-1",
        state_revision=2,
        updated_at=FIXED,
    )


def passing_checks() -> list[UpdateCheck]:
    return [
        FakeCheck(UpdateCheckKind.COMPATIBILITY, True, "compatible"),
        FakeCheck(UpdateCheckKind.MIGRATION, True, "migrated"),
        FakeCheck(UpdateCheckKind.SMOKE, True, "smoke ok"),
    ]


def make_orchestrator() -> InstanceUpdateOrchestrator:
    return InstanceUpdateOrchestrator(clock=lambda: FIXED)


def test_update_applies_only_after_all_checks_pass() -> None:
    orchestrator = make_orchestrator()
    instance = pointer()
    current = state()
    result = orchestrator.update(instance, current, image("sha256:image-v2"), passing_checks())
    assert isinstance(result, InstanceUpdateResult)
    assert result.applied is True
    assert result.blocked_at is None
    assert result.image_before.digest == "sha256:image-v1"
    assert result.image_after.digest == "sha256:image-v2"
    assert instance.image.digest == "sha256:image-v2"
    assert instance.snapshot_revision == 2
    assert result.retained_snapshot is not None
    assert result.retained_snapshot.image.digest == "sha256:image-v1"
    assert result.retained_snapshot.state.state_revision == 2
    assert result.rollback_available is True
    assert result.external_side_effects_not_reverted is True
    assert len(result.outcomes) == 3


@pytest.mark.parametrize(
    "kind",
    [
        UpdateCheckKind.COMPATIBILITY,
        UpdateCheckKind.MIGRATION,
        UpdateCheckKind.SMOKE,
    ],
)
def test_update_blocked_when_any_check_fails(kind: UpdateCheckKind) -> None:
    orchestrator = make_orchestrator()
    instance = pointer()
    checks: list[UpdateCheck] = [
        FakeCheck(UpdateCheckKind.COMPATIBILITY, True),
        FakeCheck(UpdateCheckKind.MIGRATION, True),
        FakeCheck(UpdateCheckKind.SMOKE, True),
    ]
    for check in checks:
        if check.kind is kind:
            check.ok = False
            check.message = f"{kind.value} failure"
    result = orchestrator.update(instance, state(), image("sha256:image-v2"), checks)
    assert result.applied is False
    assert result.blocked_at is kind
    assert result.image_after.digest == "sha256:image-v1"
    assert instance.image.digest == "sha256:image-v1"
    assert result.retained_snapshot is None
    assert result.rollback_available is False
    assert f"{kind.value} failure" in result.message
    assert orchestrator.snapshots() == []


def test_update_requires_all_three_check_kinds() -> None:
    orchestrator = make_orchestrator()
    result = orchestrator.update(
        instance=pointer(),
        state=state(),
        candidate=image("sha256:image-v2"),
        checks=[FakeCheck(UpdateCheckKind.COMPATIBILITY, True)],
    )
    assert result.applied is False
    assert result.blocked_at is UpdateCheckKind.MIGRATION
    assert "missing migration" in result.message


def test_duplicate_check_kind_raises() -> None:
    orchestrator = make_orchestrator()
    with pytest.raises(ValueError):
        orchestrator.update(
            instance=pointer(),
            state=state(),
            candidate=image("sha256:image-v2"),
            checks=[
                FakeCheck(UpdateCheckKind.COMPATIBILITY, True),
                FakeCheck(UpdateCheckKind.COMPATIBILITY, True),
                FakeCheck(UpdateCheckKind.MIGRATION, True),
                FakeCheck(UpdateCheckKind.SMOKE, True),
            ],
        )


def test_rollback_restores_prior_pointer_and_snapshot() -> None:
    orchestrator = make_orchestrator()
    instance = pointer()
    current = state()
    current.conversation = []
    update = orchestrator.update(instance, current, image("sha256:image-v2"), passing_checks())
    assert update.retained_snapshot is not None

    snapshot_state = update.retained_snapshot.state
    current.conversation = []
    pre_rollback_revision = current.state_revision
    result = orchestrator.rollback(instance, current, update.retained_snapshot)
    assert isinstance(result, InstanceRollbackResult)
    assert result.rolled_back is True
    assert result.image_before.digest == "sha256:image-v2"
    assert result.image_after.digest == "sha256:image-v1"
    assert instance.image.digest == "sha256:image-v1"
    assert current.state_revision == pre_rollback_revision + 1
    assert current.state_revision > snapshot_state.state_revision
    assert result.external_side_effects_not_reverted is True
    assert "external side effects cannot be reverted" in result.message


def test_rollback_retains_snapshot_in_orchestrator() -> None:
    orchestrator = make_orchestrator()
    instance = pointer()
    current = state()
    first = orchestrator.update(instance, current, image("sha256:image-v2"), passing_checks())
    second = orchestrator.update(instance, current, image("sha256:image-v3"), passing_checks())
    assert len(orchestrator.snapshots()) == 2
    assert first.retained_snapshot is not None
    assert second.retained_snapshot is not None
    assert first.retained_snapshot.image.digest == "sha256:image-v1"
    assert second.retained_snapshot.image.digest == "sha256:image-v2"
    orchestrator.rollback(instance, current, first.retained_snapshot)
    assert len(orchestrator.snapshots()) == 2


def test_rollback_to_unretained_snapshot_raises() -> None:
    orchestrator = make_orchestrator()
    instance = pointer()
    update = orchestrator.update(instance, state(), image("sha256:image-v2"), passing_checks())
    unretained = update.retained_snapshot.model_copy(update={"snapshot_id": "snap-other"})
    assert unretained is not None
    with pytest.raises(SnapshotNotRetainedError):
        orchestrator.rollback(instance, state(), unretained)


def test_rollback_across_instances_raises() -> None:
    orchestrator = make_orchestrator()
    instance = pointer()
    update = orchestrator.update(instance, state(), image("sha256:image-v2"), passing_checks())
    foreign = update.retained_snapshot.model_copy(update={"instance_id": "inst-other"})
    assert foreign is not None
    with pytest.raises(RollbackTargetMismatchError):
        orchestrator.rollback(instance, state(), foreign)
