"""Authenticated capability draft endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from zana_core.api.deps import UnitOfWorkDep, verify_token
from zana_core.api.errors import http_error
from zana_core.api.schemas import CapabilityCreate, CapabilityRead, CapabilityUpdate
from zana_core.db.models import Capability

router = APIRouter(
    prefix="/api/v1/capabilities",
    tags=["capabilities"],
    dependencies=[Depends(verify_token)],
)


@router.get("", response_model=list[CapabilityRead])
def list_capabilities(uow: UnitOfWorkDep) -> list[Capability]:
    return uow.capabilities.list_by_updated_at_desc()


@router.post("", response_model=CapabilityRead, status_code=201)
def create_capability(payload: CapabilityCreate, uow: UnitOfWorkDep) -> Capability:
    capability = Capability(
        name=payload.name,
        version=payload.version,
        manifest_json=payload.manifest_json,
    )
    created = uow.capabilities.add(capability)
    uow.session.flush()
    return created


@router.get("/{capability_id}", response_model=CapabilityRead)
def get_capability(capability_id: int, uow: UnitOfWorkDep) -> Capability:
    capability = uow.capabilities.get(capability_id)
    if capability is None:
        raise http_error(
            404,
            "CAPABILITY_NOT_FOUND",
            "No capability exists with this id.",
            actions=["list_capabilities"],
        )
    return capability


@router.put("/{capability_id}", response_model=CapabilityRead)
def update_capability(
    capability_id: int,
    payload: CapabilityUpdate,
    uow: UnitOfWorkDep,
) -> Capability:
    capability = uow.capabilities.get(capability_id)
    if capability is None:
        raise http_error(
            404,
            "CAPABILITY_NOT_FOUND",
            "No capability exists with this id.",
            actions=["list_capabilities"],
        )
    if payload.name is not None:
        capability.name = payload.name
    if payload.version is not None:
        capability.version = payload.version
    if payload.manifest_json is not None:
        capability.manifest_json = payload.manifest_json
    capability.updated_at = datetime.now(UTC)
    return capability
