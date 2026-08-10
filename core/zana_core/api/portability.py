"""Authenticated product portability routes for image export/verify/import/delete."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request, Response
from starlette.status import (
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_422_UNPROCESSABLE_ENTITY,
    HTTP_428_PRECONDITION_REQUIRED,
    HTTP_504_GATEWAY_TIMEOUT,
    HTTP_507_INSUFFICIENT_STORAGE,
)

from zana_core.api.deps import verify_token
from zana_core.api.errors import http_error
from zana_core.api.portability_schemas import (
    ExportImageRequest,
    ImportImageRequest,
    PortabilityDeleteRead,
    PortabilityExportRead,
    PortabilityImportRead,
    PortabilityVerifyRead,
)
from zana_core.portability.models import PortabilityError
from zana_core.portability.service import PortabilityProductService

router = APIRouter(
    prefix="/api/v1/images",
    tags=["portability"],
    dependencies=[Depends(verify_token)],
)


def _service(request: Request) -> PortabilityProductService:
    service = getattr(request.app.state, "portability_service", None)
    if service is None:
        data_root = Path(request.app.state.data_root)
        service = PortabilityProductService(
            request.app.state.session_factory,
            data_root,
        )
        request.app.state.portability_service = service
    return service


def _raise_portability(error: PortabilityError) -> None:
    status = _status_for(error.code)
    actions = [error.recovery_action] if error.recovery_action else []
    raise http_error(
        status,
        error.code,
        error.message,
        recoverable=True,
        actions=actions,
    ) from None


def _status_for(code: str) -> int:
    if code in ("IMAGE_NOT_FOUND", "PATH_NOT_FOUND"):
        return HTTP_404_NOT_FOUND
    if code in (
        "IMAGE_IN_USE",
        "IMPORT_CONFLICT",
        "REGISTRY_MISMATCH",
        "BASE_MODEL_MISMATCH",
        "LAYOUT_CONFLICT",
        "CONCURRENT_OPERATION",
        "STALE_REPLACE_TOKEN",
        "REPLACE_PRECONDITION_FAILED",
        "CANCELLED",
        "REPORT_EXISTS",
        "RESULT_PATH_INVALID",
        "RESULT_PATH_ESCAPE",
        "CLEANUP_UNCERTAIN",
        "LAYOUT_METADATA_INVALID",
    ):
        return HTTP_409_CONFLICT
    if code in ("APPROVAL_REQUIRED", "DELETE_CONFIRMATION_REQUIRED"):
        return HTTP_428_PRECONDITION_REQUIRED
    if code == "DISK_INSUFFICIENT":
        return HTTP_507_INSUFFICIENT_STORAGE
    if code == "DEADLINE_EXCEEDED":
        return HTTP_504_GATEWAY_TIMEOUT
    return HTTP_422_UNPROCESSABLE_ENTITY


@router.post("/{digest}/verify", response_model=PortabilityVerifyRead)
def verify_image(digest: str, request: Request) -> PortabilityVerifyRead:
    try:
        result = _service(request).verify(digest)
    except PortabilityError as error:
        _raise_portability(error)
    return PortabilityVerifyRead(
        digest=result.digest,
        status=result.status,
        runnable=result.runnable,
        runnable_reason=result.runnable_reason,
        base_model_digest=result.base_model_digest,
        base_model_available=result.base_model_available,
        layout_source=result.layout_source,
    )


@router.post("/{digest}/export", response_model=PortabilityExportRead)
def export_image(
    digest: str,
    payload: ExportImageRequest,
    request: Request,
) -> PortabilityExportRead:
    try:
        result = _service(request).export(
            digest,
            output_path=payload.output_path,
            codec=payload.codec,
            replace_token=payload.replace_token,
            replace_allowed=payload.replace_allowed,
            user_approved=payload.user_approved,
            deadline_seconds=payload.deadline_seconds,
        )
    except PortabilityError as error:
        _raise_portability(error)
    export = result.result
    return PortabilityExportRead(
        operation_id=export.operation_id,
        digest=digest,
        archive_path=result.relative_path,
        archive_digest=export.archive_digest,
        report_path=result.report_relative_path,
        report_digest=result.report_digest,
        codec=export.codec,
        stages=[stage.value for stage in export.stages],
        durability_uncertain=result.durability_uncertain,
        report_written=result.report_written,
        report_warning=result.report_warning,
        cleanup_uncertain=result.cleanup_uncertain,
    )


@router.post("/import", response_model=PortabilityImportRead)
def import_image(
    payload: ImportImageRequest,
    request: Request,
    response: Response,
) -> PortabilityImportRead:
    try:
        result = _service(request).import_archive(
            local_path=payload.local_path,
            codec=payload.codec,
            user_approved=payload.user_approved,
            deadline_seconds=payload.deadline_seconds,
        )
    except PortabilityError as error:
        _raise_portability(error)
    imported = result.result
    registration = imported.registration
    response.status_code = 201 if result.created else 200
    return PortabilityImportRead(
        operation_id=imported.operation_id,
        digest=registration.image_digest,
        config_digest=registration.config_digest,
        codec=imported.codec,
        runnable=registration.runnable,
        runnable_reason=registration.runnable_reason,
        base_model_digest=registration.base_model_digest,
        base_model_available=result.base_model_available,
        archive_digest=imported.archive_digest,
        idempotent=result.idempotent,
        created=result.created,
        artifact_count=result.artifact_count,
    )


@router.delete("/{digest}", response_model=PortabilityDeleteRead)
def delete_image(
    digest: str,
    request: Request,
    confirmed: bool = Query(default=False),
) -> PortabilityDeleteRead:
    try:
        result = _service(request).delete(digest, confirmed=confirmed)
    except PortabilityError as error:
        _raise_portability(error)
    return PortabilityDeleteRead(
        digest=result.digest,
        deleted=result.deleted,
        artifacts_retained=result.artifacts_retained,
    )
