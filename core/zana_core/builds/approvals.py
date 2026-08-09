"""Approval gate model/service with fail-closed offline/denied behavior."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from zana_core.builds.models import (
    ApprovalRequirement,
    ApprovalScope,
    BuildPlan,
    LifecyclePhase,
)


class ApprovalError(ValueError):
    """Base approval failure."""


class ApprovalExpiredError(ApprovalError):
    """Raised when approval has expired."""


class PlanChangedApprovalInvalidError(ApprovalError):
    """Raised when a changed plan invalidates prior approval."""


class OfflineDeniedError(ApprovalError):
    """Raised when offline/denied policy prevents acquisition or training."""


def required_approvals(plan: BuildPlan) -> list[ApprovalScope]:
    """Return scopes required by a plan, always including permissions and disk."""
    scopes = [ApprovalScope.PERMISSIONS, ApprovalScope.DISK_ESTIMATE]
    if plan.inputs.requires_downloads:
        scopes.append(ApprovalScope.DOWNLOAD)
    if plan.inputs.requires_training:
        scopes.append(ApprovalScope.TRAINING)
    return scopes


class ApprovalService:
    """Grants and validates exact plan-scoped approvals."""

    def __init__(
        self,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._now = now

    def create_requirement(
        self,
        *,
        approval_id: str,
        scope: ApprovalScope,
        plan: BuildPlan,
        expires_at: datetime | None = None,
    ) -> ApprovalRequirement:
        return ApprovalRequirement(
            approval_id=approval_id,
            scope=scope,
            plan_digest=plan.plan_digest,
            artifact_digests=list(plan.inputs.model_dump().get("required_artifacts", [])),
            expires_at=expires_at,
        )

    def grant(
        self,
        requirement: ApprovalRequirement,
        *,
        plan: BuildPlan,
        offline: bool = False,
    ) -> ApprovalRequirement:
        if requirement.plan_digest != plan.plan_digest:
            raise PlanChangedApprovalInvalidError(
                "The plan changed after this approval was created; approval is invalid."
            )
        if requirement.scope in (ApprovalScope.DOWNLOAD, ApprovalScope.TRAINING) and offline:
            raise OfflineDeniedError(
                f"{requirement.scope.value} is denied while the build is offline."
            )
        if requirement.expires_at is not None and requirement.expires_at < self._now():
            raise ApprovalExpiredError("This approval has expired.")
        data = requirement.model_dump()
        data["granted"] = True
        data["granted_at"] = self._now()
        return ApprovalRequirement(**data)

    def validate_current_approvals(
        self,
        *,
        plan: BuildPlan,
        requirements: list[ApprovalRequirement],
        offline: bool = False,
    ) -> None:
        required = required_approvals(plan)
        for scope in required:
            matches = [
                item
                for item in requirements
                if item.scope == scope and item.plan_digest == plan.plan_digest
            ]
            if not matches or not all(item.granted for item in matches):
                raise ApprovalError(f"Missing current approval for scope {scope.value}.")
        if offline and plan.inputs.requires_downloads:
            raise OfflineDeniedError("Downloads require online mode with explicit approval.")
        if offline and plan.inputs.requires_training:
            raise OfflineDeniedError("Training requires online mode with explicit approval.")

    def ensure_phase_can_start(
        self,
        *,
        phase: LifecyclePhase,
        plan: BuildPlan,
        requirements: list[ApprovalRequirement],
        offline: bool = False,
    ) -> None:
        if phase in (LifecyclePhase.ACQUIRING_APPROVED_ARTIFACTS, LifecyclePhase.TRAINING_ADAPTER):
            self.validate_current_approvals(
                plan=plan,
                requirements=requirements,
                offline=offline,
            )
