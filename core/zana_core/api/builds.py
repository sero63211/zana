"""Authenticated build job endpoints backed by real persistence."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from zana_core.api.deps import UnitOfWorkDep, verify_token
from zana_core.api.errors import http_error
from zana_core.api.schemas import AnalyzeBuildRequest, BuildJobRead
from zana_core.db.models import BuildJob
from zana_core.jobs.services import BuildJobNotFoundError, BuildJobService
from zana_core.jobs.state_machine import InvalidJobTransitionError

router = APIRouter(
    prefix="/api/v1/builds",
    tags=["builds"],
    dependencies=[Depends(verify_token)],
)


@router.post("/analyze", response_model=BuildJobRead, status_code=201)
def analyze_build(payload: AnalyzeBuildRequest, uow: UnitOfWorkDep) -> BuildJob:
    if uow.capabilities.get(payload.capability_id) is None:
        raise http_error(
            404,
            "CAPABILITY_NOT_FOUND",
            "No capability exists with this id.",
            recoverable=True,
            actions=["list_capabilities"],
        )
    if uow.models.get(payload.model_key) is None:
        raise http_error(
            404,
            "MODEL_NOT_AVAILABLE",
            "Required base model is not available.",
            recoverable=True,
            actions=["refresh_models", "select_other_model"],
        )
    service = BuildJobService(uow)
    return service.create_analysis_job(
        capability_id=payload.capability_id,
        model_key=payload.model_key,
        policy=payload.policy_json,
    )


@router.get("/{build_job_id}", response_model=BuildJobRead)
def get_build_job(build_job_id: int, uow: UnitOfWorkDep) -> BuildJob:
    try:
        return BuildJobService(uow).get_build_job(build_job_id)
    except BuildJobNotFoundError:
        raise http_error(
            404,
            "BUILD_JOB_NOT_FOUND",
            "No build job exists with this id.",
            actions=["list_capabilities"],
        ) from None


@router.post("/{build_job_id}/cancel", response_model=BuildJobRead)
def cancel_build_job(build_job_id: int, uow: UnitOfWorkDep) -> BuildJob:
    service = BuildJobService(uow)
    try:
        return service.cancel_build_job(build_job_id)
    except BuildJobNotFoundError:
        raise http_error(
            404,
            "BUILD_JOB_NOT_FOUND",
            "No build job exists with this id.",
            actions=["list_capabilities"],
        ) from None
    except InvalidJobTransitionError:
        raise http_error(
            409,
            "INVALID_TRANSITION",
            "This build job cannot be cancelled from its current state.",
            recoverable=True,
            actions=["get_build_job"],
        ) from None
