"""Authenticated canonical model descriptor endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError

from zana_core.acquisition.endpoints import EndpointError, validate_endpoint
from zana_core.acquisition.models import (
    AcquisitionKind,
    AcquisitionPolicy,
    AdmissionResult,
    NativeAcquisitionRequest,
)
from zana_core.acquisition.redact import (
    sanitize_job_payload,
    sanitize_terminal_error,
)
from zana_core.acquisition.supervisor import DispatchError, QueueFullError
from zana_core.api.deps import UnitOfWorkDep, verify_token
from zana_core.api.errors import http_error
from zana_core.api.schemas import JobRead, ModelPullCreate, ModelRead
from zana_core.db.models import Job, Model
from zana_core.db.unit_of_work import UnitOfWork
from zana_core.domain.enums import JobKind, JobStatus, RuntimeKind, RuntimeStatus
from zana_core.jobs.services import JobService
from zana_core.jobs.state_machine import TERMINAL_JOB_STATES
from zana_core.runtimes.discovery_service import runtime_identity

router = APIRouter(
    prefix="/api/v1/models",
    tags=["models"],
    dependencies=[Depends(verify_token)],
)


def _build_acquisition_request(
    *,
    endpoint: str,
    model_reference: str,
    expected_size_bytes: int | None,
    user_approved: bool,
    deadline_seconds: float,
) -> NativeAcquisitionRequest:
    """Build and validate the typed native acquisition request."""
    return NativeAcquisitionRequest(
        kind=AcquisitionKind.OLLAMA_PULL,
        endpoint=endpoint,
        model_reference=model_reference,
        policy=AcquisitionPolicy.LOCAL_ONLY,
        expected_size_bytes=expected_size_bytes,
        user_approved=user_approved,
        deadline_seconds=deadline_seconds,
    )


def _admission_error(result: AdmissionResult) -> HTTPException:
    """Map a bounded admission denial to a canonical client error."""
    mapping = {
        "UNKNOWN_HEADROOM": (
            "DISK_HEADROOM_UNKNOWN",
            "Disk headroom could not be measured.",
            ["check_disk_space"],
        ),
        "HEADROOM_UNAVAILABLE": (
            "DISK_HEADROOM_UNAVAILABLE",
            "Disk headroom is unavailable.",
            ["check_disk_space"],
        ),
        "UNKNOWN_SIZE": (
            "DISK_REQUIREMENT_UNKNOWN",
            "The model size is unknown; provide an exact estimate.",
            ["provide_model_size"],
        ),
        "INVALID_SIZE": (
            "INVALID_MODEL_SIZE",
            "The model size must be a positive exact byte count.",
            ["provide_model_size"],
        ),
        "DISK_INSUFFICIENT": (
            "DISK_INSUFFICIENT",
            "There is not enough free disk space for this model.",
            ["free_disk", "retry_pull"],
        ),
        "LEASE_CONFLICT": (
            "ACQUISITION_LEASE_CONFLICT",
            "Another resource lease conflicts with this acquisition.",
            ["wait_for_lease", "retry_pull"],
        ),
    }
    code, message, actions = mapping.get(
        result.reason,
        (
            "ACQUISITION_BLOCKED",
            "Model acquisition was blocked by resource policy.",
            ["retry_pull"],
        ),
    )
    return http_error(
        422,
        code,
        message,
        recoverable=True,
        actions=actions,
    )


@router.post("/pull", response_model=JobRead, status_code=201)
def pull_model(
    payload: ModelPullCreate,
    uow: UnitOfWorkDep,
    fastapi_request: Request,
) -> Job:
    """Approve, preflight, persist, and dispatch one bounded model pull."""
    runtime = uow.runtimes.get(payload.runtime_id)
    if runtime is None:
        raise http_error(
            404,
            "RUNTIME_NOT_FOUND",
            "No runtime exists with this id.",
            actions=["list_runtimes"],
        )
    if runtime.kind != RuntimeKind.OLLAMA:
        raise http_error(
            422,
            "UNSUPPORTED_RUNTIME_PULL",
            "Only an Ollama runtime supports a native model pull.",
            recoverable=True,
            actions=["select_ollama_runtime", "refresh_discovery"],
        )
    if not payload.user_approved:
        raise http_error(
            422,
            "USER_APPROVAL_REQUIRED",
            "Native model acquisition requires explicit user approval.",
            recoverable=True,
            actions=["confirm_model_download"],
        )
    try:
        normalized_endpoint = validate_endpoint(
            runtime.endpoint,
            AcquisitionPolicy.LOCAL_ONLY,
        )
    except EndpointError:
        raise http_error(
            422,
            "INVALID_ENDPOINT",
            "The runtime endpoint is not valid for a local native pull.",
            recoverable=True,
            actions=["fix_runtime_endpoint", "select_local_runtime"],
        ) from None
    try:
        acquisition_request = _build_acquisition_request(
            endpoint=normalized_endpoint,
            model_reference=payload.model_reference,
            expected_size_bytes=payload.expected_size_bytes,
            user_approved=payload.user_approved,
            deadline_seconds=payload.deadline_seconds,
        )
    except ValidationError:
        raise http_error(
            422,
            "INVALID_MODEL_REFERENCE",
            "The model reference is invalid for a native pull.",
            recoverable=True,
            actions=["fix_model_reference"],
        ) from None

    if runtime.status in {RuntimeStatus.OFFLINE, RuntimeStatus.ERROR}:
        raise http_error(
            409,
            "RUNTIME_NOT_ENABLED",
            "The runtime is not enabled for a native model pull.",
            recoverable=True,
            actions=["refresh_discovery", "start_runtime"],
        )

    admission = fastapi_request.app.state.acquisition_admission
    admitted = admission.admit(acquisition_request)
    if not admitted.allowed:
        raise _admission_error(admitted)

    service = JobService(uow)
    job = service.create_job(
        JobKind.MODEL_PULL,
        phase="queued",
        message=payload.model_reference,
    )
    job.error_json = {
        **sanitize_job_payload(
            runtime_id=runtime.id,
            model_reference=payload.model_reference,
            expected_size_bytes=payload.expected_size_bytes,
            user_approved=payload.user_approved,
            deadline_seconds=payload.deadline_seconds,
            runtime_kind=runtime.kind.value,
            runtime_source=runtime.source.value,
            runtime_status=runtime.status.value,
            runtime_identity=runtime_identity(
                runtime.kind,
                runtime.endpoint,
                runtime.source,
            ),
        )
    }
    uow.commit()
    supervisor = fastapi_request.app.state.acquisition_supervisor
    try:
        supervisor.dispatch(job.id)
    except QueueFullError:
        _mark_dispatch_failed(
            uow,
            job.id,
            "ACQUISITION_QUEUE_FULL",
            "The acquisition queue is full; retry after the active pull finishes.",
        )
        raise http_error(
            503,
            "ACQUISITION_QUEUE_FULL",
            "The acquisition queue is full; retry after the active pull finishes.",
            recoverable=True,
            actions=["retry_pull", "list_jobs"],
            details={"job_id": job.id},
        ) from None
    except DispatchError:
        _mark_dispatch_failed(
            uow,
            job.id,
            "ACQUISITION_DISPATCH_FAILED",
            "The queued acquisition could not be dispatched.",
        )
        raise http_error(
            503,
            "ACQUISITION_DISPATCH_FAILED",
            "The queued acquisition could not be dispatched.",
            recoverable=True,
            actions=["retry_pull"],
            details={"job_id": job.id},
        ) from None
    uow.session.expire(job)
    return job


def _mark_dispatch_failed(
    uow: UnitOfWork,
    job_id: int,
    code: str,
    message: str,
) -> None:
    """Persist an honest terminal state instead of a fake queued success."""
    service = JobService(uow)
    try:
        service.transition_job(
            job_id,
            JobStatus.FAILED,
            phase="failed",
            message=message,
            error=sanitize_terminal_error(code=code, message=message),
        )
    except Exception:  # noqa: BLE001 - caller commits the persisted job row
        job = uow.jobs.get(job_id)
        if job is not None and job.status not in TERMINAL_JOB_STATES:
            job.status = JobStatus.FAILED
            job.phase = "failed"
            job.message = message
            job.error_json = sanitize_terminal_error(code=code, message=message)
    uow.commit()


@router.get("", response_model=list[ModelRead])
def list_models(
    uow: UnitOfWorkDep,
    runtime: int | None = Query(default=None),
    capability: str | None = Query(default=None),
    runnable: bool | None = Query(default=None),
) -> list[Model]:
    if runtime is not None:
        return uow.models.list_by_runtime(runtime)
    if capability is not None:
        return uow.models.list_by_capability(capability)
    if runnable:
        return uow.models.list_runnable()
    return uow.models.list()


@router.get("/{model_key:path}", response_model=ModelRead)
def get_model(model_key: str, uow: UnitOfWorkDep) -> Model:
    model = uow.models.get(model_key)
    if model is None:
        raise http_error(
            404,
            "MODEL_NOT_AVAILABLE",
            "No model descriptor exists with this key.",
            recoverable=True,
            actions=["refresh_models", "select_other_model"],
        )
    return model
