"""Authenticated generic job and event endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from zana_core.api.deps import UnitOfWorkDep, verify_token
from zana_core.api.errors import http_error
from zana_core.api.schemas import JobEventRead, JobRead
from zana_core.db.models import Job, JobEvent
from zana_core.jobs.services import JobNotFoundError, JobService

router = APIRouter(
    prefix="/api/v1/jobs",
    tags=["jobs"],
    dependencies=[Depends(verify_token)],
)


@router.get("/{job_id}", response_model=JobRead)
def get_job(job_id: int, uow: UnitOfWorkDep) -> Job:
    try:
        return JobService(uow).get_job(job_id)
    except JobNotFoundError:
        raise http_error(
            404,
            "JOB_NOT_FOUND",
            "No job exists with this id.",
            actions=["list_capabilities"],
        ) from None


@router.get("/{job_id}/events", response_model=list[JobEventRead])
def list_job_events(job_id: int, uow: UnitOfWorkDep) -> list[JobEvent]:
    try:
        return JobService(uow).list_events(job_id)
    except JobNotFoundError:
        raise http_error(
            404,
            "JOB_NOT_FOUND",
            "No job exists with this id.",
            actions=["list_capabilities"],
        ) from None
