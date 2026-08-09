"""Authenticated canonical model descriptor endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError

from zana_core.acquisition.endpoints import EndpointError, validate_endpoint
from zana_core.acquisition.models import (
    AcquisitionKind,
    AcquisitionPolicy,
    NativeAcquisitionPlan,
    NativeAcquisitionRequest,
    OllamaPullBody,
)
from zana_core.api.deps import UnitOfWorkDep, verify_token
from zana_core.api.errors import http_error
from zana_core.api.schemas import JobRead, ModelPullCreate, ModelRead
from zana_core.db.models import Job, Model
from zana_core.domain.enums import JobKind, RuntimeKind
from zana_core.jobs.services import JobService

router = APIRouter(
    prefix="/api/v1/models",
    tags=["models"],
    dependencies=[Depends(verify_token)],
)


def _prepare_pull_plan(
    *,
    endpoint: str,
    model_reference: str,
    expected_size_bytes: int | None,
    user_approved: bool,
    deadline_seconds: float,
) -> tuple[NativeAcquisitionRequest, NativeAcquisitionPlan]:
    """Build and validate the typed native acquisition request/plan."""
    request = NativeAcquisitionRequest(
        kind=AcquisitionKind.OLLAMA_PULL,
        endpoint=endpoint,
        model_reference=model_reference,
        policy=AcquisitionPolicy.LOCAL_ONLY,
        expected_size_bytes=expected_size_bytes,
        user_approved=user_approved,
        deadline_seconds=deadline_seconds,
    )
    plan = NativeAcquisitionPlan(
        kind=AcquisitionKind.OLLAMA_PULL,
        endpoint=request.endpoint,
        model_reference=request.model_reference,
        body=OllamaPullBody(model=request.model_reference, stream=True),
    )
    return request, plan


@router.post("/pull", response_model=JobRead, status_code=201)
def pull_model(
    payload: ModelPullCreate,
    uow: UnitOfWorkDep,
) -> Job:
    """Record a persisted runtime-native acquisition request as a job.

    The request is validated and persisted with its bounded native plan, but
    model bytes are never proxied and no pull is started here.
    """
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
        request, plan = _prepare_pull_plan(
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

    service = JobService(uow)
    job = service.create_job(
        JobKind.MODEL_PULL,
        phase="queued",
        message=payload.model_reference,
    )
    job.error_json = {
        "code": "ACQUISITION_QUEUED",
        "message": "Native model acquisition queued; not started.",
        "request": request.model_dump(mode="json"),
        "plan": plan.model_dump(mode="json"),
    }
    return job


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
