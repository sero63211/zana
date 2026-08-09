"""Approval requirements and offline/deny fail-closed behavior."""

from __future__ import annotations

from tests.planning.helpers import plan_inputs, policy
from zana_core.planning.models import (
    AcquisitionMode,
    ApprovalKind,
    ApprovalRequirement,
    ApprovalSet,
    DownloadMode,
)
from zana_core.planning.planner import BuildPlanner


def plan(**inputs):
    return BuildPlanner().plan(**plan_inputs(**inputs))


def test_pending_approvals_prevent_execution():
    result = plan()
    kinds = {requirement.kind for requirement in result.approvals.requirements}
    assert kinds == {ApprovalKind.PERMISSIONS, ApprovalKind.DISK, ApprovalKind.TRAINING}
    assert result.approvals.all_granted is False
    assert result.approvable is False


def test_all_required_approvals_make_plan_approvable():
    requirements = (
        ApprovalRequirement(kind=ApprovalKind.PERMISSIONS, description="permissions"),
        ApprovalRequirement(kind=ApprovalKind.DISK, description="disk"),
        ApprovalRequirement(kind=ApprovalKind.TRAINING, description="training"),
    )
    result = plan(
        approvals=ApprovalSet.with_grants(
            requirements=requirements,
            granted={
                ApprovalKind.PERMISSIONS,
                ApprovalKind.DISK,
                ApprovalKind.TRAINING,
            },
        )
    )
    assert result.approvable is True


def test_download_approval_required_when_checkpoint_missing():
    result = plan(
        model=plan_inputs()["model"].model_copy(update={"training_source_available": False})
    )
    kinds = {requirement.kind for requirement in result.approvals.requirements}
    assert ApprovalKind.DOWNLOAD in kinds
    assert result.approvable is False


def test_offline_mode_blocks_download_required_plan():
    result = plan(
        policy=policy(
            acquisition=AcquisitionMode.OFFLINE,
            allow_external_artifact_downloads=DownloadMode.OFFLINE,
        ),
        model=plan_inputs()["model"].model_copy(update={"training_source_available": False}),
    )
    assert any("NETWORK_OFFLINE_DOWNLOAD" in blocker for blocker in result.blockers)
    assert result.approvable is False


def test_downloads_allowed_removes_download_approval_requirement():
    result = plan(
        policy=policy(allow_external_artifact_downloads=DownloadMode.ALLOWED),
        model=plan_inputs()["model"].model_copy(update={"training_source_available": False}),
    )
    download = next(
        item for item in result.approvals.requirements if item.kind == ApprovalKind.DOWNLOAD
    )
    assert download.required is False
    assert "explicitly allowed" in download.reason


def test_no_training_no_training_approval():
    capability = plan_inputs()["capability"].model_copy(
        update={
            "has_training": False,
            "training_goal": None,
            "train_record_count": None,
            "validation_record_count": None,
            "training_files_present": False,
            "has_tools": False,
            "has_knowledge": True,
            "knowledge_citation_required": True,
            "knowledge_bytes": 5 * 1024 * 1024,
        }
    )
    result = plan(capability=capability)
    kinds = {requirement.kind for requirement in result.approvals.requirements}
    assert ApprovalKind.TRAINING not in kinds
    assert ApprovalKind.DOWNLOAD not in kinds


def test_approval_set_missing_and_all_granted_properties():
    approvals = ApprovalSet.with_grants(
        (
            ApprovalRequirement(kind=ApprovalKind.DISK, description="disk"),
            ApprovalRequirement(kind=ApprovalKind.TRAINING, description="training"),
        ),
        {ApprovalKind.DISK},
    )
    assert approvals.all_granted is False
    assert approvals.missing == (ApprovalKind.TRAINING,)
