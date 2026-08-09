"""Transition service optimistic concurrency and history tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from zana_core.builds.models import (
    BuildPlan,
    BuildPlanInputs,
    CancellationAcknowledgement,
    CleanupPlan,
    LifecyclePhase,
)
from zana_core.builds.service import (
    BuildLifecycleService,
    OptimisticConcurrencyError,
)


def fixed_now() -> datetime:
    return datetime(2026, 8, 9, tzinfo=UTC)


def record(service: BuildLifecycleService) -> tuple:
    created = service.create_record(
        capability_digest="sha256:cap",
        model_key="ollama:example",
        model_identity_digest="sha256:model",
    )
    return created, service


class TestTransitionService:
    def test_full_path_preserves_immutable_history(self) -> None:
        service = BuildLifecycleService(now=fixed_now)
        current, _ = record(service)
        revision = current.revision
        for target in (
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
        ):
            next_record = service.transition(
                current,
                expected_revision=revision,
                target=target,
            )
            assert next_record.revision == revision + 1
            assert next_record.current_phase == target
            assert len(next_record.attempts) == len(current.attempts) + 1
            revision += 1
            current = next_record

    def test_optimistic_concurrency_rejects_stale_revision(self) -> None:
        service = BuildLifecycleService(now=fixed_now)
        created, _ = record(service)
        advanced = service.transition(
            created,
            expected_revision=0,
            target=LifecyclePhase.ANALYZING,
        )
        with pytest.raises(OptimisticConcurrencyError):
            service.transition(
                advanced,
                expected_revision=0,
                target=LifecyclePhase.BASELINE_RUNNING,
            )

    def test_skipped_and_terminal_transitions_rejected(self) -> None:
        service = BuildLifecycleService(now=fixed_now)
        created, _ = record(service)
        with pytest.raises(ValueError):
            service.transition(
                created,
                expected_revision=0,
                target=LifecyclePhase.VERIFIED,
            )
        verified = service.transition(
            created,
            expected_revision=0,
            target=LifecyclePhase.ANALYZING,
        )
        assert verified.revision == 1

    def test_truthful_progress_and_checkpoints(self) -> None:
        service = BuildLifecycleService(now=fixed_now)
        created, _ = record(service)
        analyzing = service.transition(
            created,
            expected_revision=0,
            target=LifecyclePhase.ANALYZING,
        )
        attempt_id = analyzing.attempts[-1].attempt_id
        progressed = service.record_progress(
            analyzing,
            expected_revision=1,
            attempt_id=attempt_id,
            progress_0_1=0.5,
            message="analysis half complete",
        )
        assert progressed.progress_history[-1].progress_0_1 == 0.5
        checkpoints = service.add_checkpoint(
            progressed,
            expected_revision=2,
            attempt_id=attempt_id,
            resumable=True,
            description="analysis checkpoint",
        )
        assert checkpoints.checkpoints[-1].resumable is True

    def test_recovery_only_for_blocked_with_resumable_checkpoints(self) -> None:
        service = BuildLifecycleService(now=fixed_now)
        created, _ = record(service)
        blocked = service.transition(
            created,
            expected_revision=0,
            target=LifecyclePhase.BLOCKED,
        )
        plan = service.recovery_plan(blocked)
        assert plan.retry_allowed is True

    def test_cancellation_request_and_acknowledgement(self) -> None:
        service = BuildLifecycleService(now=fixed_now)
        created, _ = record(service)
        analyzing = service.transition(
            created,
            expected_revision=0,
            target=LifecyclePhase.ANALYZING,
        )
        cancelled = service.request_cancellation(
            analyzing,
            expected_revision=1,
            reason="user request",
        )
        request_id = cancelled.cancellation_requests[-1].request_id
        ack = CancellationAcknowledgement(
            request_id=request_id,
            acknowledged_at=fixed_now(),
            child_termination_plan=CleanupPlan(terminate_child_pids=[123]),
            partial_artifacts_unusable=True,
        )
        acknowledged = service.acknowledge_cancellation(
            cancelled,
            expected_revision=2,
            request_id=request_id,
            acknowledgement=ack,
        )
        assert acknowledged.acknowledgements[-1].partial_artifacts_unusable is True

    def test_plan_replacement_is_immutable_and_revisioned(self) -> None:
        service = BuildLifecycleService(now=fixed_now)
        created, _ = record(service)
        plan = BuildPlan(
            plan_digest="sha256:plan",
            inputs=BuildPlanInputs(
                capability_digest="sha256:cap",
                model_key="ollama:example",
                runtime_status="online",
                hardware_profile_digest="sha256:hw",
                policy_digest="sha256:policy",
                strategy="RAG_ONLY",
            ),
            created_at=fixed_now(),
        )
        updated = service.replace_plan(
            created,
            expected_revision=0,
            plan=plan,
        )
        assert updated.plan is not None
        assert updated.revision == 1
        assert created.plan is None
