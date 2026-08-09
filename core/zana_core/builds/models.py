"""Strict immutable typed build lifecycle models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LifecyclePhase(str, Enum):
    """Named phases aligned with the build lifecycle spec."""

    DRAFT = "DRAFT"
    ANALYZING = "ANALYZING"
    BASELINE_RUNNING = "BASELINE_RUNNING"
    PLANNED = "PLANNED"
    ACQUIRING_APPROVED_ARTIFACTS = "ACQUIRING_APPROVED_ARTIFACTS"
    BUILDING_KNOWLEDGE = "BUILDING_KNOWLEDGE"
    TRAINING_ADAPTER = "TRAINING_ADAPTER"
    MATERIALIZING = "MATERIALIZING"
    EVALUATING = "EVALUATING"
    PACKING = "PACKING"
    VERIFIED = "VERIFIED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"


class PhaseAttempt(BaseModel):
    """One immutable attempt of a phase."""

    model_config = ConfigDict(frozen=True)

    attempt_id: str
    phase: LifecyclePhase
    started_at: datetime
    finished_at: datetime | None = None
    progress_0_1: float = Field(default=0.0, ge=0, le=1)
    message: str = ""
    acknowledged_cancellation: bool = False


class Checkpoint(BaseModel):
    """An explicitly resumable checkpoint within one phase attempt."""

    model_config = ConfigDict(frozen=True)

    checkpoint_id: str
    phase: LifecyclePhase
    attempt_id: str
    resumable: bool = False
    description: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class Failure(BaseModel):
    """Immutable phase failure with recovery plan and partial-artifact status."""

    model_config = ConfigDict(frozen=True)

    code: str
    message: str
    recoverable: bool = False
    partial_artifacts_unusable: bool = True
    actions: list[str] = Field(default_factory=list)
    phase: LifecyclePhase


class RecoveryPlan(BaseModel):
    """Retry/resume instructions emitted as data, never executed here."""

    model_config = ConfigDict(frozen=True)

    retry_allowed: bool
    resume_checkpoints: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    reason: str = ""


class FinalizationPlan(BaseModel):
    """Explicit digest-verify/move/register intent; no side effects executed."""

    model_config = ConfigDict(frozen=True)

    verify_digests_first: bool = True
    atomic_move_intent: list[str] = Field(default_factory=list)
    transactional_image_registration_intent: bool = True
    image_digest: str | None = None
    external_side_effects_claimed_rolled_back: bool = False


class CleanupPlan(BaseModel):
    """Data-only cleanup and finalization actions for later integration."""

    model_config = ConfigDict(frozen=True)

    remove_paths: list[str] = Field(default_factory=list)
    retain_paths: list[str] = Field(default_factory=list)
    terminate_child_pids: list[int] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ProgressUpdate(BaseModel):
    """Truthful progress update for one phase attempt."""

    model_config = ConfigDict(frozen=True)

    attempt_id: str
    phase: LifecyclePhase
    progress_0_1: float = Field(ge=0, le=1)
    message: str = ""


class CancellationRequest(BaseModel):
    """Explicit request to cancel a build at an active phase."""

    model_config = ConfigDict(frozen=True)

    request_id: str
    phase: LifecyclePhase
    reason: str = ""
    requested_at: datetime


class CancellationAcknowledgement(BaseModel):
    """Phase runner acknowledges a cancellation request."""

    model_config = ConfigDict(frozen=True)

    request_id: str
    acknowledged_at: datetime
    child_termination_plan: CleanupPlan
    partial_artifacts_unusable: bool = True


class ApprovalScope(str, Enum):
    """Typed approval scopes required before acquisition/training."""

    DOWNLOAD = "download"
    TRAINING = "training"
    PERMISSIONS = "permissions"
    DISK_ESTIMATE = "disk_estimate"


class ApprovalRequirement(BaseModel):
    """Typed approval requirement with scope and immutable inputs."""

    model_config = ConfigDict(frozen=True)

    approval_id: str
    scope: ApprovalScope
    plan_digest: str
    artifact_digests: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None
    granted: bool = False
    granted_at: datetime | None = None


class BuildPlanInputs(BaseModel):
    """Inputs required to plan a build, recorded immutably."""

    model_config = ConfigDict(frozen=True)

    capability_digest: str
    model_key: str
    model_identity_digest: str | None = None
    runtime_status: str
    hardware_profile_digest: str
    policy_digest: str
    evaluation_suite_digest: str | None = None
    strategy: str
    reasons: list[str] = Field(default_factory=list)
    estimated_disk_bytes: int | None = None
    requires_downloads: bool = False
    requires_training: bool = False


class BuildPlan(BaseModel):
    """Immutable user-reviewable build plan."""

    model_config = ConfigDict(frozen=True)

    plan_digest: str
    inputs: BuildPlanInputs
    created_at: datetime


class BuildLifecycleRecord(BaseModel):
    """Full immutable lifecycle record with ordered history."""

    model_config = ConfigDict(frozen=True)

    record_id: str
    capability_digest: str
    model_key: str
    model_identity_digest: str | None = None
    created_at: datetime
    revision: int
    current_phase: LifecyclePhase
    attempts: list[PhaseAttempt] = Field(default_factory=list)
    checkpoints: list[Checkpoint] = Field(default_factory=list)
    approvals: list[ApprovalRequirement] = Field(default_factory=list)
    plan: BuildPlan | None = None
    failures: list[Failure] = Field(default_factory=list)
    cancellation_requests: list[CancellationRequest] = Field(default_factory=list)
    acknowledgements: list[CancellationAcknowledgement] = Field(default_factory=list)
    finalization: FinalizationPlan | None = None
    progress_history: list[ProgressUpdate] = Field(default_factory=list)
