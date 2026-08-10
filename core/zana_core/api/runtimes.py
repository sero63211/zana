"""Authenticated runtime registry endpoints."""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request, Response
from starlette.status import HTTP_204_NO_CONTENT

from zana_core.api.deps import UnitOfWorkDep, verify_token
from zana_core.api.errors import http_error
from zana_core.api.schemas import JobRead, RuntimeCreate, RuntimeRead
from zana_core.db.models import Job, Runtime
from zana_core.domain.enums import (
    JobKind,
    JobStatus,
    RuntimeSource,
    RuntimeStatus,
)
from zana_core.jobs.services import JobService
from zana_core.runtimes.discovery_service import RuntimeDiscoveryService

router = APIRouter(
    prefix="/api/v1/runtimes",
    tags=["runtimes"],
    dependencies=[Depends(verify_token)],
)


def _validate_manual_endpoint(endpoint: str) -> None:
    parsed = urlsplit(endpoint)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise http_error(
            422,
            "INVALID_ENDPOINT",
            "A manual runtime endpoint must be an absolute http(s) URL.",
            recoverable=True,
            actions=["fix_endpoint"],
        )
    if parsed.username is not None or parsed.password is not None:
        raise http_error(
            422,
            "ENDPOINT_CREDENTIALS_NOT_ALLOWED",
            "Do not embed credentials in runtime endpoints.",
            recoverable=True,
            actions=["store_credentials_separately"],
        )


@router.post("/refresh", response_model=JobRead)
def refresh_runtimes(request: Request, uow: UnitOfWorkDep) -> Job:
    """Run bounded runtime discovery and persist the real results as a job.

    Discovery persistence runs inside a savepoint so a failed probe cannot
    leave partial runtime/model rows, while the FAILED job and its event still
    commit and remain fetchable from the jobs API.
    """
    discovery: RuntimeDiscoveryService = request.app.state.discovery_service
    service = JobService(uow)
    job = service.create_job(
        JobKind.RUNTIME_REFRESH,
        phase="discovery",
        message="Refreshing runtime and model discovery.",
    )
    service.transition_job(job.id, JobStatus.RUNNING, phase="discovery")
    try:
        with uow.session.begin_nested():
            descriptors = discovery.refresh(uow)
        return service.transition_job(
            job.id,
            JobStatus.SUCCEEDED,
            phase="complete",
            message=f"Runtime discovery complete; {len(descriptors)} candidate(s) probed.",
        )
    except Exception:  # noqa: BLE001 - failures are sanitized below
        return service.transition_job(
            job.id,
            JobStatus.FAILED,
            phase="failed",
            message="Runtime discovery could not complete.",
            error={
                "code": "RUNTIME_REFRESH_FAILED",
                "message": "Runtime discovery could not complete.",
                "recoverable": True,
                "actions": ["retry_refresh"],
            },
        )


@router.get("", response_model=list[RuntimeRead])
def list_runtimes(uow: UnitOfWorkDep) -> list[Runtime]:
    return uow.runtimes.list()


@router.post("/manual", response_model=RuntimeRead, status_code=201)
def create_manual_runtime(payload: RuntimeCreate, uow: UnitOfWorkDep) -> Runtime:
    _validate_manual_endpoint(payload.endpoint)
    existing = uow.runtimes.get_by_endpoint(payload.endpoint, RuntimeSource.MANUAL)
    if existing is not None:
        raise http_error(
            409,
            "RUNTIME_ALREADY_EXISTS",
            "A manual runtime with this endpoint already exists.",
            recoverable=True,
            actions=["list_runtimes"],
        )
    runtime = Runtime(
        kind=payload.kind,
        endpoint=payload.endpoint,
        source=RuntimeSource.MANUAL,
        status=RuntimeStatus.UNKNOWN,
        metadata_json=payload.metadata_json,
    )
    created = uow.runtimes.add(runtime)
    uow.session.flush()
    return created


@router.delete("/{runtime_id}", status_code=HTTP_204_NO_CONTENT)
def delete_runtime(runtime_id: int, uow: UnitOfWorkDep) -> Response:
    runtime = uow.runtimes.get(runtime_id)
    if runtime is None:
        raise http_error(
            404,
            "RUNTIME_NOT_FOUND",
            "No runtime exists with this id.",
            actions=["list_runtimes"],
        )
    if runtime.source != RuntimeSource.MANUAL:
        raise http_error(
            409,
            "CANNOT_DELETE_AUTO_RUNTIME",
            "Only manually configured runtimes can be removed.",
            recoverable=True,
            actions=["disable_runtime_instead"],
        )
    uow.runtimes.delete(runtime)
    return Response(status_code=HTTP_204_NO_CONTENT)
