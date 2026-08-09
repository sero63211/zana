"""Authenticated ZANA Image registry endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from zana_core.api.deps import UnitOfWorkDep, verify_token
from zana_core.api.errors import http_error
from zana_core.api.schemas import ImageRead
from zana_core.db.models import Image

router = APIRouter(
    prefix="/api/v1/images",
    tags=["images"],
    dependencies=[Depends(verify_token)],
)


@router.get("", response_model=list[ImageRead])
def list_images(uow: UnitOfWorkDep) -> list[Image]:
    return uow.images.list()


@router.get("/{digest}", response_model=ImageRead)
def get_image(digest: str, uow: UnitOfWorkDep) -> Image:
    image = uow.images.get(digest)
    if image is None:
        raise http_error(
            404,
            "IMAGE_NOT_FOUND",
            "No image exists with this digest.",
            actions=["list_images"],
        )
    return image
