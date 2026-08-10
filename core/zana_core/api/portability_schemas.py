"""Isolated request/response models for the portability product API."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from zana_core.images.archive import ArchiveFormat
from zana_core.images.models import RunnableState


class ExportImageRequest(BaseModel):
    """Explicit output path and codec selection for one image export."""

    model_config = ConfigDict(extra="forbid", strict=True)

    output_path: Annotated[str, Field(strict=True, min_length=1, max_length=4096)]
    codec: ArchiveFormat = ArchiveFormat.TAR
    replace_token: Annotated[str | None, Field(strict=True, max_length=200)] = None
    replace_allowed: Annotated[bool, Field(strict=True)] = False
    user_approved: Annotated[bool, Field(strict=True)] = False
    deadline_seconds: Annotated[float, Field(strict=True, gt=0, le=3600)] = 300.0

    @field_validator("codec", mode="before")
    @classmethod
    def coerce_codec(cls, value: object) -> object:
        if type(value) is ArchiveFormat:
            return value
        if type(value) is str:
            return ArchiveFormat(value)
        raise ValueError("codec must be an archive format")


class ImportImageRequest(BaseModel):
    """User-approved local archive import request."""

    model_config = ConfigDict(extra="forbid", strict=True)

    local_path: Annotated[str, Field(strict=True, min_length=1, max_length=4096)]
    codec: ArchiveFormat | None = None
    user_approved: Annotated[bool, Field(strict=True)] = False
    deadline_seconds: Annotated[float, Field(strict=True, gt=0, le=3600)] = 300.0

    @field_validator("codec", mode="before")
    @classmethod
    def coerce_codec(cls, value: object) -> object:
        if value is None:
            return None
        if type(value) is ArchiveFormat:
            return value
        if type(value) is str:
            return ArchiveFormat(value)
        raise ValueError("codec must be an archive format or null")


class PortabilityVerifyRead(BaseModel):
    """Actionable verify status without host paths or secrets."""

    model_config = ConfigDict(extra="forbid", strict=True)

    digest: str
    status: str
    runnable: RunnableState
    runnable_reason: str
    base_model_digest: str | None = None
    base_model_available: bool
    layout_source: str


class PortabilityExportRead(BaseModel):
    """Export result with a data-root-relative archive path."""

    model_config = ConfigDict(extra="forbid", strict=True)

    operation_id: str
    digest: str
    archive_path: str
    archive_digest: str
    codec: ArchiveFormat
    stages: list[str]
    durability_uncertain: bool


class PortabilityImportRead(BaseModel):
    """Import result with exact runnability and idempotency state."""

    model_config = ConfigDict(extra="forbid", strict=True)

    operation_id: str
    digest: str
    config_digest: str
    codec: ArchiveFormat
    runnable: RunnableState
    runnable_reason: str
    base_model_digest: str | None = None
    base_model_available: bool
    archive_digest: str
    idempotent: bool
    created: bool
    artifact_count: int


class PortabilityDeleteRead(BaseModel):
    """Delete result; immutable blobs are intentionally retained."""

    model_config = ConfigDict(extra="forbid", strict=True)

    digest: str
    deleted: bool
    artifacts_retained: bool
