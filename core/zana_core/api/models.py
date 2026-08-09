"""Authenticated canonical model descriptor endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from zana_core.api.deps import UnitOfWorkDep, verify_token
from zana_core.api.errors import http_error
from zana_core.api.schemas import ModelRead
from zana_core.db.models import Model

router = APIRouter(
    prefix="/api/v1/models",
    tags=["models"],
    dependencies=[Depends(verify_token)],
)


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


@router.get("/{model_key}", response_model=ModelRead)
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
