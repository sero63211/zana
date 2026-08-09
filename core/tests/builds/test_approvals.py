"""Approval gate, invalidation, and offline fail-closed tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from zana_core.builds.approvals import (
    ApprovalError,
    ApprovalExpiredError,
    ApprovalService,
    OfflineDeniedError,
    PlanChangedApprovalInvalidError,
    required_approvals,
)
from zana_core.builds.models import (
    ApprovalScope,
    BuildPlan,
    BuildPlanInputs,
    LifecyclePhase,
)


def now() -> datetime:
    return datetime(2026, 8, 9, tzinfo=UTC)


def plan(*, requires_downloads: bool = False, requires_training: bool = False) -> BuildPlan:
    return BuildPlan(
        plan_digest="sha256:plan",
        inputs=BuildPlanInputs(
            capability_digest="sha256:cap",
            model_key="ollama:example",
            runtime_status="online",
            hardware_profile_digest="sha256:hw",
            policy_digest="sha256:policy",
            strategy="RAG_ONLY",
            requires_downloads=requires_downloads,
            requires_training=requires_training,
        ),
        created_at=now(),
    )


class TestRequiredApprovals:
    def test_scopes_follow_plan(self) -> None:
        assert required_approvals(plan()) == [
            ApprovalScope.PERMISSIONS,
            ApprovalScope.DISK_ESTIMATE,
        ]
        scopes = required_approvals(plan(requires_downloads=True, requires_training=True))
        assert ApprovalScope.DOWNLOAD in scopes
        assert ApprovalScope.TRAINING in scopes


class TestApprovalService:
    def test_grants_and_validates_current_approvals(self) -> None:
        service = ApprovalService(now=now)
        p = plan(requires_downloads=True, requires_training=True)
        requirements = []
        for index, scope in enumerate(required_approvals(p)):
            requirements.append(
                service.create_requirement(
                    approval_id=f"a{index}",
                    scope=scope,
                    plan=p,
                )
            )
        requirements = [service.grant(item, plan=p) for item in requirements]
        service.validate_current_approvals(plan=p, requirements=requirements)
        service.ensure_phase_can_start(
            phase=LifecyclePhase.TRAINING_ADAPTER,
            plan=p,
            requirements=requirements,
        )

    def test_changed_plan_invalidates_approval(self) -> None:
        service = ApprovalService(now=now)
        original = plan()
        changed = plan()
        changed_data = changed.model_dump()
        changed_data["plan_digest"] = "sha256:changed"
        changed = BuildPlan(**changed_data)
        requirement = service.create_requirement(
            approval_id="a",
            scope=ApprovalScope.DISK_ESTIMATE,
            plan=original,
        )
        with pytest.raises(PlanChangedApprovalInvalidError):
            service.grant(requirement, plan=changed)

    def test_offline_denies_downloads_and_training(self) -> None:
        service = ApprovalService(now=now)
        p = plan(requires_downloads=True, requires_training=True)
        requirements = []
        for index, scope in enumerate(required_approvals(p)):
            requirement = service.create_requirement(
                approval_id=f"a{index}",
                scope=scope,
                plan=p,
            )
            requirements.append(service.grant(requirement, plan=p))
        with pytest.raises(OfflineDeniedError):
            service.validate_current_approvals(
                plan=p,
                requirements=requirements,
                offline=True,
            )

    def test_expired_approval_fails_closed(self) -> None:
        service = ApprovalService(now=now)
        p = plan()
        requirement = service.create_requirement(
            approval_id="a",
            scope=ApprovalScope.PERMISSIONS,
            plan=p,
            expires_at=now() - timedelta(seconds=1),
        )
        with pytest.raises(ApprovalExpiredError):
            service.grant(requirement, plan=p)

    def test_missing_scope_denied(self) -> None:
        service = ApprovalService(now=now)
        p = plan(requires_downloads=True)
        permissions = service.create_requirement(
            approval_id="p",
            scope=ApprovalScope.PERMISSIONS,
            plan=p,
        )
        disk = service.create_requirement(
            approval_id="d",
            scope=ApprovalScope.DISK_ESTIMATE,
            plan=p,
        )
        requirements = [
            service.grant(permissions, plan=p),
            service.grant(disk, plan=p),
        ]
        with pytest.raises(ApprovalError):
            service.validate_current_approvals(plan=p, requirements=requirements)
