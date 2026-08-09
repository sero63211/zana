"""Authenticated runtime registry endpoints."""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Response
from starlette.status import HTTP_204_NO_CONTENT

from zana_core.api.deps import UnitOfWorkDep, verify_token
from zana_core.api.errors import http_error
from zana_core.api.schemas import RuntimeCreate, RuntimeRead
from zana_core.db.models import Runtime
from zana_core.domain.enums import RuntimeSource, RuntimeStatus

router = APIRouter(
    prefix="/api/v1/runtimes",
    tags=["runtimes"],
    dependencies=[Depends(verify_token)],
)


def _validate_manual_endpoint(endpoint: str) -> None:
    parsed = urlparse(endpoint)
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
