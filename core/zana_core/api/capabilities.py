"""Authenticated capability draft and Capability Source authoring endpoints."""

from __future__ import annotations

import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request

from zana_core.api.deps import UnitOfWorkDep, verify_token
from zana_core.api.errors import http_error
from zana_core.api.schemas import (
    CapabilityCreate,
    CapabilityDetailRead,
    CapabilityIssueRead,
    CapabilityProvenanceRead,
    CapabilityRead,
    CapabilitySourceCreate,
    CapabilitySourceRead,
    CapabilityUpdate,
    CapabilityValidationRead,
)
from zana_core.capabilities import authoring
from zana_core.capabilities.errors import CapabilityIssue, CapabilitySourceValidationError
from zana_core.capabilities.validator import CapabilitySourceValidator
from zana_core.db.models import Capability, CapabilitySource

router = APIRouter(
    prefix="/api/v1/capabilities",
    tags=["capabilities"],
    dependencies=[Depends(verify_token)],
)

_MAX_REPORT_ISSUES = 200
_MAX_REPORT_PROVENANCE = 1000


def _data_root(request: Request) -> Path:
    root = request.app.state.data_root
    if root is None:
        raise http_error(
            500,
            "DATA_ROOT_MISSING",
            "The Core app data root is not configured.",
            actions=["restart_core"],
        )
    path = Path(root)
    if not path.is_absolute():
        raise http_error(
            500,
            "DATA_ROOT_INVALID",
            "The Core app data root must be an absolute path.",
            actions=["restart_core"],
        )
    return path


def _get_capability_or_404(uow: UnitOfWorkDep, capability_id: int) -> Capability:
    capability = uow.capabilities.get(capability_id)
    if capability is None:
        raise http_error(
            404,
            "CAPABILITY_NOT_FOUND",
            "No capability exists with this id.",
            actions=["list_capabilities"],
        )
    return capability


def _workspace_for(capability: Capability, data_root: Path) -> Path:
    if not capability.working_dir:
        return authoring.capability_workspace_path(data_root, capability.id)
    workspace = Path(os.path.normpath(str(capability.working_dir)))
    if not workspace.is_absolute():
        raise http_error(
            409,
            "WORKSPACE_INVALID",
            "The saved capability workspace path is not absolute.",
            recoverable=True,
            actions=["update_capability"],
        )
    if not authoring.workspace_is_under_data_root(workspace, data_root):
        raise http_error(
            409,
            "WORKSPACE_ESCAPE",
            "The saved capability workspace is outside the app data root.",
            recoverable=True,
            actions=["update_capability"],
        )
    return workspace


def _raise_authoring(
    exc: authoring.AuthoringError,
    workspace: Path,
    data_root: Path,
    *,
    status_code: int = 422,
    actions: list[str] | None = None,
) -> None:
    details: dict[str, Any] = {}
    issues = getattr(exc, "issues", ())
    if issues:
        details["issue_count"] = len(issues)
        details["returned_issue_count"] = min(len(issues), _MAX_REPORT_ISSUES)
        details["issues"] = [
            {
                "code": issue.code,
                "message": authoring.sanitize_message(issue.message, workspace, data_root),
                "file": authoring.safe_issue_file(issue.file, workspace),
                "line": issue.line,
            }
            for issue in issues[:_MAX_REPORT_ISSUES]
        ]
    raise http_error(
        status_code,
        exc.code,
        authoring.sanitize_message(exc.message, workspace, data_root),
        recoverable=True,
        actions=actions or ["fix_source_payload"],
        details=details,
    ) from None


def _compensate_source_publish(
    *,
    staged: authoring.StagedSource,
    manifest_target: Path,
    source_backup: Path | None,
    manifest_backup: Path | None,
) -> None:
    """Restore prior files or remove newly-created ones; raises when unconfirmed."""
    if source_backup is not None:
        authoring.restore_backup(source_backup, staged.target_path)
    else:
        authoring.remove_file_target(staged.target_path)
    if manifest_backup is not None:
        authoring.restore_backup(manifest_backup, manifest_target)
    else:
        authoring.remove_file_target(manifest_target)
    if source_backup is not None:
        authoring.discard_temp(source_backup)
    if manifest_backup is not None:
        authoring.discard_temp(manifest_backup)


def _compensate_update_manifest(
    *,
    workspace: Path,
    data_root: Path,
    manifest_target: Path,
    manifest_backup: Path | None,
    workspace_existed: bool,
) -> None:
    if workspace_existed:
        if manifest_backup is None:
            authoring.remove_file_target(manifest_target)
            return
        authoring.restore_backup(manifest_backup, manifest_target)
        authoring.discard_temp(manifest_backup)
        return
    if not authoring.remove_workspace(workspace, data_root, created_by_request=True):
        raise authoring.AuthoringError(
            "ROLLBACK_UNCONFIRMED", "workspace cleanup could not be confirmed"
        )


def _raise_rollback_unconfirmed(
    workspace: Path,
    data_root: Path,
    *,
    original_code: str,
) -> None:
    raise http_error(
        500,
        "ROLLBACK_UNCONFIRMED",
        f"{original_code} failed and rollback could not be confirmed.",
        details={},
        actions=["retry_source_ingest"],
    ) from None


def _discard_new_workspace(
    workspace: Path,
    data_root: Path,
    *,
    workspace_existed: bool,
    original_code: str,
) -> None:
    """Remove a workspace only when this request created a real managed dir."""
    if workspace_existed:
        return
    try:
        info = workspace.lstat()
    except FileNotFoundError:
        return
    except OSError:
        return
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return
    if not authoring.remove_workspace(workspace, data_root, created_by_request=True):
        _raise_rollback_unconfirmed(workspace, data_root, original_code=original_code)


def _create_failure_cleanup(
    workspace: Path,
    data_root: Path,
) -> None:
    """Remove a newly-created workspace or fail honestly when unconfirmed."""
    if not authoring.remove_workspace(workspace, data_root, created_by_request=True):
        _raise_rollback_unconfirmed(workspace, data_root, original_code="CAPABILITY_CREATE")


@router.get("", response_model=list[CapabilityRead])
def list_capabilities(uow: UnitOfWorkDep) -> list[Capability]:
    return uow.capabilities.list_by_updated_at_desc()


@router.post("", response_model=CapabilityRead, status_code=201)
def create_capability(
    payload: CapabilityCreate,
    request: Request,
    uow: UnitOfWorkDep,
) -> Capability:
    """Create a draft with a real canonical workspace under the app data root."""
    data_root = _data_root(request)
    capability = Capability(
        name=payload.name,
        version=payload.version,
        manifest_json=payload.manifest_json,
    )
    uow.capabilities.add(capability)
    uow.session.flush()
    workspace = authoring.capability_workspace_path(data_root, capability.id)
    workspace_existed = workspace.exists()
    if workspace_existed:
        raise http_error(
            409,
            "WORKSPACE_EXISTS",
            "A capability workspace already exists at the canonical path; no draft was created.",
            recoverable=True,
            actions=["list_capabilities"],
        )
    try:
        authoring.ensure_workspace(workspace, data_root)
        manifest = (
            payload.manifest_json
            if payload.manifest_json
            else authoring.default_manifest(payload.name, payload.version, capability.id)
        )
        authoring.write_manifest(workspace, manifest)
        capability.working_dir = str(workspace)
        capability.manifest_json = manifest
        uow.commit()
    except authoring.AuthoringError as exc:
        _create_failure_cleanup(workspace, data_root)
        _raise_authoring(
            exc,
            workspace,
            data_root,
            status_code=500,
            actions=["fix_capability_manifest", "retry_create"],
        )
    except OSError as exc:
        _create_failure_cleanup(workspace, data_root)
        raise http_error(
            500,
            "WORKSPACE_CREATE",
            "cannot create capability workspace",
            details={},
            actions=["retry_create"],
        ) from exc
    except Exception:
        removed = authoring.remove_workspace(workspace, data_root, created_by_request=True)
        if not removed:
            _raise_rollback_unconfirmed(workspace, data_root, original_code="DATABASE_COMMIT")
        raise http_error(
            500,
            "DATABASE_COMMIT_FAILED",
            "Capability creation could not be committed; the new workspace was removed.",
            details={},
            actions=["retry_create"],
        ) from None
    return capability


@router.get("/{capability_id}", response_model=CapabilityRead)
def get_capability(capability_id: int, uow: UnitOfWorkDep) -> Capability:
    return _get_capability_or_404(uow, capability_id)


@router.put("/{capability_id}", response_model=CapabilityRead)
def update_capability(
    capability_id: int,
    payload: CapabilityUpdate,
    request: Request,
    uow: UnitOfWorkDep,
) -> Capability:
    """Update draft metadata; manifest updates stay coherent with zana.yaml."""
    capability = _get_capability_or_404(uow, capability_id)
    if payload.manifest_json is not None:
        data_root = _data_root(request)
        workspace = _workspace_for(capability, data_root)
        workspace_existed = workspace.exists()
        manifest_target = workspace / "zana.yaml"
        manifest_backup = None
        try:
            if not workspace_existed:
                authoring.ensure_workspace(workspace, data_root)
            manifest_backup = authoring.stage_backup(manifest_target)
        except authoring.AuthoringError as exc:
            # Backup failed before any publication; leave the untouched
            # manifest exactly as it was and never run compensation.
            if workspace_existed:
                _raise_authoring(
                    exc,
                    workspace,
                    data_root,
                    actions=["fix_capability_manifest"],
                )
            if authoring.remove_workspace(workspace, data_root, created_by_request=True):
                _raise_authoring(
                    exc,
                    workspace,
                    data_root,
                    actions=["fix_capability_manifest"],
                )
            _raise_rollback_unconfirmed(workspace, data_root, original_code="MANIFEST_UPDATE")
        try:
            if payload.manifest_json:
                authoring.write_manifest(workspace, payload.manifest_json)
            else:
                authoring.remove_manifest(workspace)
            capability.manifest_json = payload.manifest_json
            if payload.name is not None:
                capability.name = payload.name
            if payload.version is not None:
                capability.version = payload.version
            capability.updated_at = datetime.now(UTC)
            if not capability.working_dir:
                capability.working_dir = str(workspace)
            uow.commit()
        except authoring.AuthoringError as exc:
            try:
                _compensate_update_manifest(
                    workspace=workspace,
                    data_root=data_root,
                    manifest_target=manifest_target,
                    manifest_backup=manifest_backup,
                    workspace_existed=workspace_existed,
                )
            except authoring.AuthoringError:
                _raise_rollback_unconfirmed(workspace, data_root, original_code="MANIFEST_UPDATE")
            _raise_authoring(
                exc,
                workspace,
                data_root,
                actions=["fix_capability_manifest"],
            )
        except OSError as exc:
            try:
                _compensate_update_manifest(
                    workspace=workspace,
                    data_root=data_root,
                    manifest_target=manifest_target,
                    manifest_backup=manifest_backup,
                    workspace_existed=workspace_existed,
                )
            except authoring.AuthoringError:
                _raise_rollback_unconfirmed(workspace, data_root, original_code="MANIFEST_UPDATE")
            raise http_error(
                500,
                "MANIFEST_WRITE",
                "cannot write the on-disk manifest",
                details={},
                actions=["retry_update"],
            ) from exc
        except Exception:
            try:
                _compensate_update_manifest(
                    workspace=workspace,
                    data_root=data_root,
                    manifest_target=manifest_target,
                    manifest_backup=manifest_backup,
                    workspace_existed=workspace_existed,
                )
            except authoring.AuthoringError:
                _raise_rollback_unconfirmed(workspace, data_root, original_code="DATABASE_COMMIT")
            raise http_error(
                500,
                "DATABASE_COMMIT_FAILED",
                "Capability update could not be committed; the prior manifest was restored.",
                details={},
                actions=["retry_update"],
            ) from None
        capability.manifest_json = payload.manifest_json
        if not capability.working_dir:
            capability.working_dir = str(workspace)
        if manifest_backup is not None:
            authoring.discard_temp(manifest_backup)
    else:
        if payload.name is not None:
            capability.name = payload.name
        if payload.version is not None:
            capability.version = payload.version
        capability.updated_at = datetime.now(UTC)
    return capability


@router.get("/{capability_id}/sources", response_model=list[CapabilitySourceRead])
def list_capability_sources(
    capability_id: int,
    uow: UnitOfWorkDep,
) -> list[CapabilitySource]:
    capability = _get_capability_or_404(uow, capability_id)
    sources = uow.capability_sources.list_for_capability(capability.id)
    return sorted(sources, key=lambda source: source.local_path)


@router.get("/{capability_id}/detail", response_model=CapabilityDetailRead)
def get_capability_detail(
    capability_id: int,
    request: Request,
    uow: UnitOfWorkDep,
) -> CapabilityDetailRead:
    """Typed detail/source response; never full host paths or document contents."""
    capability = _get_capability_or_404(uow, capability_id)
    data_root = _data_root(request)
    workspace = _workspace_for(capability, data_root)
    sources = sorted(
        uow.capability_sources.list_for_capability(capability.id),
        key=lambda source: source.local_path,
    )
    return CapabilityDetailRead(
        id=capability.id,
        name=capability.name,
        version=capability.version,
        manifest_json=capability.manifest_json,
        workspace_relative=authoring.relative_workspace_path(workspace, data_root),
        sources=[CapabilitySourceRead.model_validate(source) for source in sources],
        created_at=capability.created_at,
        updated_at=capability.updated_at,
    )


@router.post("/{capability_id}/sources", response_model=CapabilitySourceRead, status_code=201)
def add_capability_source(
    capability_id: int,
    payload: CapabilitySourceCreate,
    request: Request,
    uow: UnitOfWorkDep,
) -> CapabilitySource:
    """Ingest one bounded source with atomic replacement and coherent manifest."""
    capability = _get_capability_or_404(uow, capability_id)
    data_root = _data_root(request)
    workspace = _workspace_for(capability, data_root)
    workspace_existed = workspace.exists()
    source_request = authoring.SourceRequest(
        kind=payload.kind,
        content=getattr(payload, "content", None),
        local_path=getattr(payload, "local_path", None),
        user_approved=getattr(payload, "user_approved", False),
        eval_kind=getattr(payload, "eval_kind", None),
    )
    try:
        authoring.ensure_workspace(workspace, data_root)
        staged = authoring.stage_source(workspace, source_request)
    except authoring.AuthoringError as exc:
        _discard_new_workspace(
            workspace,
            data_root,
            workspace_existed=workspace_existed,
            original_code="SOURCE_INGEST",
        )
        _raise_authoring(exc, workspace, data_root)

    manifest = capability.manifest_json if capability.manifest_json else None
    try:
        on_disk = authoring.load_manifest_dict(workspace)
    except authoring.AuthoringError as exc:
        staged.cleanup()
        _discard_new_workspace(
            workspace,
            data_root,
            workspace_existed=workspace_existed,
            original_code="SOURCE_INGEST",
        )
        _raise_authoring(
            exc,
            workspace,
            data_root,
            actions=["fix_capability_manifest"],
        )
    if manifest is not None and (on_disk is None or manifest != on_disk):
        staged.cleanup()
        _discard_new_workspace(
            workspace,
            data_root,
            workspace_existed=workspace_existed,
            original_code="SOURCE_INGEST",
        )
        raise http_error(
            409,
            "MANIFEST_DIVERGED",
            "On-disk zana.yaml is missing or differs from the persisted manifest_json; "
            "update the manifest before adding sources.",
            recoverable=True,
            actions=["update_capability_manifest"],
        )
    if manifest is None:
        manifest = (
            on_disk
            if on_disk is not None
            else authoring.default_manifest(capability.name, capability.version, capability.id)
        )
    try:
        new_manifest = authoring.update_manifest_for_source(
            manifest,
            manifest_kind=staged.manifest_kind,
            eval_kind=staged.eval_kind,
        )
        manifest_temp = authoring.stage_manifest(workspace, new_manifest)
    except authoring.AuthoringError as exc:
        staged.cleanup()
        _discard_new_workspace(
            workspace,
            data_root,
            workspace_existed=workspace_existed,
            original_code="SOURCE_INGEST",
        )
        _raise_authoring(
            exc,
            workspace,
            data_root,
            actions=["fix_capability_manifest"],
        )

    try:
        uow.capability_sources.delete_for_capability_and_path(capability.id, staged.relative_path)
        row = uow.capability_sources.add(
            CapabilitySource(
                capability_id=capability.id,
                original_name=staged.original_name,
                local_path=staged.relative_path,
                sha256=staged.sha256,
                media_type=staged.media_type,
                size_bytes=staged.size_bytes,
                metadata_json=staged.metadata,
            )
        )
        uow.session.flush()
    except Exception:
        staged.cleanup()
        authoring.discard_temp(manifest_temp)
        _discard_new_workspace(
            workspace,
            data_root,
            workspace_existed=workspace_existed,
            original_code="SOURCE_INGEST",
        )
        raise

    manifest_target = workspace / "zana.yaml"
    try:
        source_backup: Path | None = None
        manifest_backup: Path | None = None
        source_backup = authoring.stage_backup(staged.target_path)
        manifest_backup = authoring.stage_backup(manifest_target)
    except authoring.AuthoringError as exc:
        staged.cleanup()
        authoring.discard_temp(manifest_temp)
        if source_backup is not None:
            authoring.discard_temp(source_backup)
        _discard_new_workspace(
            workspace,
            data_root,
            workspace_existed=workspace_existed,
            original_code="SOURCE_INGEST",
        )
        _raise_authoring(
            exc,
            workspace,
            data_root,
            status_code=500,
            actions=["retry_source_ingest"],
        )

    try:
        authoring.publish_staged(staged.temp_path, staged.target_path)
        authoring.publish_staged(manifest_temp, manifest_target)
    except Exception:
        # Roll the replaced or newly-created on-disk targets back so a failed
        # publish never leaves files that disagree with the DB.
        staged.cleanup()
        authoring.discard_temp(manifest_temp)
        try:
            _compensate_source_publish(
                staged=staged,
                manifest_target=manifest_target,
                source_backup=source_backup,
                manifest_backup=manifest_backup,
            )
            if not workspace_existed and not authoring.remove_workspace(
                workspace, data_root, created_by_request=True
            ):
                _raise_rollback_unconfirmed(workspace, data_root, original_code="SOURCE_INGEST")
        except authoring.AuthoringError:
            _raise_rollback_unconfirmed(workspace, data_root, original_code="SOURCE_INGEST")
        raise http_error(
            500,
            "SOURCE_PUBLISH",
            "Source publication failed; the prior good source was preserved.",
            details={},
            actions=["retry_source_ingest"],
        ) from None
    try:
        capability.manifest_json = new_manifest
        capability.updated_at = datetime.now(UTC)
        if not capability.working_dir:
            capability.working_dir = str(workspace)
        uow.commit()
    except Exception:
        staged.cleanup()
        authoring.discard_temp(manifest_temp)
        try:
            _compensate_source_publish(
                staged=staged,
                manifest_target=manifest_target,
                source_backup=source_backup,
                manifest_backup=manifest_backup,
            )
            if not workspace_existed and not authoring.remove_workspace(
                workspace, data_root, created_by_request=True
            ):
                _raise_rollback_unconfirmed(workspace, data_root, original_code="DATABASE_COMMIT")
        except authoring.AuthoringError:
            _raise_rollback_unconfirmed(workspace, data_root, original_code="DATABASE_COMMIT")
        raise http_error(
            500,
            "DATABASE_COMMIT_FAILED",
            "Source ingestion could not be committed; the prior source was restored.",
            details={},
            actions=["retry_source_ingest"],
        ) from None
    if source_backup is not None:
        authoring.discard_temp(source_backup)
    if manifest_backup is not None:
        authoring.discard_temp(manifest_backup)

    return row


@router.post("/{capability_id}/validate", response_model=CapabilityValidationRead)
def validate_capability(
    capability_id: int,
    request: Request,
    uow: UnitOfWorkDep,
) -> CapabilityValidationRead:
    """Run the real CapabilitySourceValidator on the saved workspace."""
    capability = _get_capability_or_404(uow, capability_id)
    data_root = _data_root(request)
    workspace = _workspace_for(capability, data_root)
    issues: list[CapabilityIssue] = []
    provenance = ()
    manifest_present = False
    preflight = authoring.validate_source_preflight(workspace, data_root)
    if not preflight.ok:
        issues.extend(preflight.issues)
    else:
        try:
            on_disk = authoring.load_manifest_dict(workspace)
        except authoring.AuthoringError as exc:
            issues.append(
                CapabilityIssue(
                    exc.code,
                    authoring.sanitize_message(exc.message, workspace, data_root),
                )
            )
            on_disk = None
        manifest_present = on_disk is not None
        if capability.manifest_json:
            if on_disk is None:
                issues.append(
                    CapabilityIssue(
                        "MANIFEST_DIVERGED",
                        "zana.yaml is missing or cannot be parsed while the database "
                        "still records a manifest.",
                    )
                )
            elif on_disk != capability.manifest_json:
                issues.append(
                    CapabilityIssue(
                        "MANIFEST_DIVERGED",
                        "On-disk zana.yaml differs from the persisted manifest_json.",
                    )
                )
        if not issues:
            try:
                result = CapabilitySourceValidator().validate(workspace)
            except CapabilitySourceValidationError as exc:
                issues.extend(exc.issues)
            except OSError:
                issues.append(
                    CapabilityIssue("VALIDATION_READ", "cannot read the capability workspace")
                )
            else:
                provenance = result.provenance

    bounded_issues = issues[:_MAX_REPORT_ISSUES]
    issue_reads = [
        CapabilityIssueRead(
            code=issue.code,
            message=authoring.sanitize_message(issue.message, workspace, data_root),
            file=authoring.safe_issue_file(issue.file, workspace),
            line=issue.line,
        )
        for issue in bounded_issues
    ]
    provenance_reads = [
        CapabilityProvenanceRead(
            relative_path=item.relative_path,
            sha256=item.sha256,
            size_bytes=item.size_bytes,
            role=item.role.value,
            title=item.title,
            title_origin=item.title_origin,
            declared_license=item.declared_license,
            usage_metadata=dict(item.usage_metadata),
            ingested_at=item.ingested_at,
            rights_inferred=item.rights_inferred,
        )
        for item in provenance[:_MAX_REPORT_PROVENANCE]
    ]
    return CapabilityValidationRead(
        capability_id=capability.id,
        root_relative=authoring.relative_workspace_path(workspace, data_root),
        manifest_present=manifest_present,
        valid=not issues,
        issue_count=len(issues),
        returned_issue_count=len(bounded_issues),
        issues=issue_reads,
        provenance=provenance_reads,
        validated_at=datetime.now(UTC),
    )
