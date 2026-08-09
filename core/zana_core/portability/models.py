"""Strict immutable request/result/stage/error models for export/import.

Codec identities and runnable state are re-exported from the canonical
``images`` stack so portability never defines a parallel vocabulary.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, datetime
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from zana_core.artifacts.digest import validate_digest
from zana_core.images.archive import ArchiveFormat as CodecKind
from zana_core.images.models import RunnableState

ABS_MAX_ARCHIVE_BYTES = 32 * 1024**3
ABS_MAX_UNPACKED_BYTES = 32 * 1024**3
ABS_MAX_MEMBER_BYTES = 16 * 1024**3
ABS_MAX_JSON_BYTES = 1024 * 1024
ABS_MAX_CHUNK_BYTES = 1024 * 1024
ABS_MAX_MEMBERS = 8192
ABS_MAX_DEPTH = 32
ABS_MAX_PATH_CHARS = 1024
ABS_MAX_DEADLINE_SECONDS = 3600.0
ABS_MAX_SLACK_BYTES = 1024 * 1024 * 1024
ABS_MAX_CODEC_METADATA_BYTES = 1024 * 1024
MAX_PATH_STRING_CHARS = 4096
MAX_DIGEST_STRING_CHARS = 200
MAX_REPLACE_TOKEN_CHARS = 200
MAX_OPERATION_ID_CHARS = 200
MAX_STAGE_ITEMS = 32
MAX_CLEANUP_PATH_ITEMS = 32
MAX_BLOB_REGISTRATIONS = 8192


class OperationStage(str, Enum):
    """Canonical export/import pipeline stages."""

    PREFLIGHT = "preflight"
    LOCK = "lock"
    VALIDATE_LAYOUT = "validate_layout"
    SECRET_SCAN = "secret_scan"
    DISK_PREFLIGHT = "disk_preflight"
    CODE_WRITE = "code_write"
    FSYNC = "fsync"
    ATOMIC_REPLACE = "atomic_replace"
    UNPACK = "unpack"
    OCI_VALIDATION = "oci_validation"
    REGISTER = "register"
    CLEANUP = "cleanup"
    COMPLETE = "complete"


class RecoveryAction:
    """Stable recovery action identifiers for structured failures."""

    FREE_DISK_SPACE = "free_disk_space"
    PROVIDE_REPLACE_TOKEN = "provide_replace_token"
    USE_SUPPORTED_CODEC = "use_supported_codec"
    REPAIR_ARCHIVE = "repair_archive"
    REMOVE_SYMLINK = "remove_symlink"
    CHOOSE_APPROVED_PATH = "choose_approved_path"
    RETRY = "retry"
    CLEAR_TEMP_WORKSPACE = "clear_temp_workspace"
    INSTALL_ZSTD = "install_zstd"


def _safe_detail_scalar(value: Any) -> str | None:
    """Map only safe builtin JSON scalars; never calls str/repr on objects."""
    if type(value) is type(None):
        return "null"
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        if abs(value) > 10**15:
            return "out-of-range"
        return str(value)
    if type(value) is float:
        if not math.isfinite(value):
            return "non-finite"
        return f"{value:.6g}"
    if type(value) is str:
        if len(value) > 500:
            return "oversized"
        return "".join(ch for ch in value if ch.isprintable())[:500]
    return "non-scalar"


def _safe_message(message: Any) -> str:
    """Accept only exact bounded strings; never call str/repr on objects."""
    if type(message) is not str:
        return "invalid structured error message"
    if len(message) > 2000:
        return "structured error message too long"
    return message


def _sanitize_details(details: Any) -> dict[str, str]:
    """Bounded-collect cap+1 from safe JSON primitives only."""
    if type(details) is not dict:
        return {}
    collected: dict[str, str] = {}
    for raw_key, raw_value in details.items():
        if len(collected) >= 16:
            break
        if type(raw_key) is not str or len(raw_key) > 100:
            continue
        safe_value = _safe_detail_scalar(raw_value)
        if safe_value is not None:
            collected[raw_key] = safe_value
    return collected


class PortabilityError(Exception):
    """Base structured failure carrying stage and recovery guidance."""

    def __init__(
        self,
        message: Any,
        *,
        code: Any,
        stage: OperationStage = OperationStage.PREFLIGHT,
        recovery_action: Any = RecoveryAction.RETRY,
        details: dict[str, Any] | None = None,
    ) -> None:
        safe = _safe_message(message)
        super().__init__(safe)
        self.message = safe
        self.code = _safe_message(code)
        self.stage = stage if type(stage) is OperationStage else OperationStage.PREFLIGHT
        self.recovery_action = _safe_message(recovery_action)
        self.details = _sanitize_details(details)


class LimitExceededError(PortabilityError):
    """A configured count, size, depth, path, or time limit was exceeded."""


class PathPolicyError(PortabilityError):
    """A path is outside approved roots, is a symlink, or is an unsafe member."""


class PreconditionError(PortabilityError):
    """A replace precondition or operation precondition failed closed."""


class DiskPreflight(BaseModel):
    """Conservative disk requirement versus observed free space."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    required_bytes: Annotated[int, Field(strict=True, ge=0, le=ABS_MAX_ARCHIVE_BYTES * 4)]
    available_bytes: Annotated[int, Field(strict=True, ge=0, le=ABS_MAX_ARCHIVE_BYTES * 4)]
    sufficient: Annotated[bool, Field(strict=True)]
    path: Annotated[str, Field(strict=True, max_length=MAX_PATH_STRING_CHARS)]
    note: Annotated[str, Field(strict=True, max_length=500)] = ""


class PortabilityLimits(BaseModel):
    """Bounded input/count/depth/path/time limits for every operation.

    The count, per-member, unpacked-size, depth, path, and deadline fields are
    forwarded to the canonical archive extractor; the remaining fields bound
    the service itself. Limits are frozen and capped so callers cannot request
    unbounded or internally inconsistent work.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    max_archive_bytes: Annotated[int, Field(strict=True, gt=0, le=ABS_MAX_ARCHIVE_BYTES)] = (
        2 * 1024**3
    )
    max_member_bytes: Annotated[int, Field(strict=True, gt=0, le=ABS_MAX_MEMBER_BYTES)] = (
        512 * 1024**2
    )
    max_unpacked_bytes: Annotated[int, Field(strict=True, gt=0, le=ABS_MAX_UNPACKED_BYTES)] = (
        2 * 1024**3
    )
    max_members: Annotated[int, Field(strict=True, gt=0, le=ABS_MAX_MEMBERS)] = 4096
    max_depth: Annotated[int, Field(strict=True, gt=0, le=ABS_MAX_DEPTH)] = 32
    max_path_chars: Annotated[int, Field(strict=True, gt=0, le=ABS_MAX_PATH_CHARS)] = 1024
    max_json_bytes: Annotated[int, Field(strict=True, gt=0, le=ABS_MAX_JSON_BYTES)] = 512 * 1024
    chunk_size: Annotated[int, Field(strict=True, gt=0, le=ABS_MAX_CHUNK_BYTES)] = 1024 * 1024
    min_free_slack_bytes: Annotated[int, Field(strict=True, ge=0, le=ABS_MAX_SLACK_BYTES)] = (
        64 * 1024**2
    )
    codec_metadata_bytes_per_member: Annotated[
        int, Field(strict=True, ge=0, le=ABS_MAX_CODEC_METADATA_BYTES)
    ] = 2048
    gzip_expansion_factor: Annotated[
        float, Field(strict=True, ge=1.0, le=10.0, allow_inf_nan=False)
    ] = 2.0
    deadline_seconds: Annotated[
        float, Field(strict=True, gt=0, le=ABS_MAX_DEADLINE_SECONDS, allow_inf_nan=False)
    ] = 300.0

    @model_validator(mode="after")
    def _cross_field_bounds(self) -> PortabilityLimits:
        if self.max_member_bytes > self.max_unpacked_bytes:
            raise ValueError("max_member_bytes must not exceed max_unpacked_bytes")
        if self.max_unpacked_bytes > self.max_archive_bytes:
            raise ValueError("max_unpacked_bytes must not exceed max_archive_bytes")
        if self.max_json_bytes > self.max_unpacked_bytes:
            raise ValueError("max_json_bytes must not exceed max_unpacked_bytes")
        if self.chunk_size > self.max_member_bytes:
            raise ValueError("chunk_size must not exceed max_member_bytes")
        return self


class ExportRequest(BaseModel):
    """Immutable request to export a validated OCI layout to an archive."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    operation_id: Annotated[
        str, Field(strict=True, min_length=1, max_length=MAX_OPERATION_ID_CHARS)
    ]
    layout_path: Annotated[str, Field(strict=True, max_length=MAX_PATH_STRING_CHARS)]
    destination: Annotated[str, Field(strict=True, max_length=MAX_PATH_STRING_CHARS)]
    codec: CodecKind = CodecKind.TAR
    replace_token: Annotated[str | None, Field(strict=True, max_length=MAX_REPLACE_TOKEN_CHARS)] = (
        None
    )
    replace_allowed: Annotated[bool, Field(strict=True)] = False
    limits: PortabilityLimits = Field(default_factory=PortabilityLimits)


class ImportRequest(BaseModel):
    """Immutable request to import an archive into the artifact store."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    operation_id: Annotated[
        str, Field(strict=True, min_length=1, max_length=MAX_OPERATION_ID_CHARS)
    ]
    source: Annotated[str, Field(strict=True, max_length=MAX_PATH_STRING_CHARS)]
    codec: CodecKind | None = None
    limits: PortabilityLimits = Field(default_factory=PortabilityLimits)


class CleanupEvidence(BaseModel):
    """Paths removed by this operation; never includes user data."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    removed_paths: tuple[
        Annotated[str, Field(strict=True, max_length=MAX_PATH_STRING_CHARS)], ...
    ] = Field(default_factory=tuple, max_length=MAX_CLEANUP_PATH_ITEMS)
    workspace_removed: Annotated[bool, Field(strict=True)] = False
    destination_touched: Annotated[bool, Field(strict=True)] = False
    cleanup_failures: tuple[Annotated[str, Field(strict=True, max_length=500)], ...] = Field(
        default_factory=tuple, max_length=MAX_CLEANUP_PATH_ITEMS
    )


class BlobRegistration(BaseModel):
    """One immutable blob registration intent with digest and presence."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    digest: Annotated[str, Field(strict=True, max_length=MAX_DIGEST_STRING_CHARS)]
    size_bytes: Annotated[int, Field(strict=True, ge=0, le=ABS_MAX_UNPACKED_BYTES)]
    already_present: Annotated[bool, Field(strict=True)] = False

    @field_validator("digest")
    @classmethod
    def validate_blob_digest(cls, value: str) -> str:
        return validate_digest(value)


class RegistrationDbIntent(BaseModel):
    """Typed DB registration intent; never writes a database."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    table: str = "images"
    image_digest: Annotated[str, Field(strict=True, max_length=MAX_DIGEST_STRING_CHARS)]
    config_digest: Annotated[str, Field(strict=True, max_length=MAX_DIGEST_STRING_CHARS)]
    verification_status: str = "unverified"
    base_model_digest: Annotated[
        str | None, Field(strict=True, max_length=MAX_DIGEST_STRING_CHARS)
    ] = None
    runnable: RunnableState

    @field_validator("image_digest", "config_digest")
    @classmethod
    def validate_digests(cls, value: str) -> str:
        return validate_digest(value)

    @field_validator("base_model_digest")
    @classmethod
    def validate_optional_digest(cls, value: str | None) -> str | None:
        if value is not None:
            return validate_digest(value)
        return None


class RegistrationPlan(BaseModel):
    """DB registration boundary: exact intents without writing any DB/API."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    image_digest: Annotated[str, Field(strict=True, max_length=MAX_DIGEST_STRING_CHARS)]
    config_digest: Annotated[str, Field(strict=True, max_length=MAX_DIGEST_STRING_CHARS)]
    manifest_digest: Annotated[str, Field(strict=True, max_length=MAX_DIGEST_STRING_CHARS)]
    blobs: tuple[BlobRegistration, ...] = Field(
        default_factory=tuple, max_length=MAX_BLOB_REGISTRATIONS
    )
    runnable: RunnableState
    runnable_reason: Annotated[str, Field(strict=True, max_length=1000)]
    base_model_digest: Annotated[
        str | None, Field(strict=True, max_length=MAX_DIGEST_STRING_CHARS)
    ] = None
    db_intent: RegistrationDbIntent

    @field_validator("image_digest", "config_digest", "manifest_digest")
    @classmethod
    def validate_digests(cls, value: str) -> str:
        return validate_digest(value)

    @field_validator("base_model_digest")
    @classmethod
    def validate_optional_digest(cls, value: str | None) -> str | None:
        if value is not None:
            return validate_digest(value)
        return None


class ExportResult(BaseModel):
    """Structured export outcome with digest, stages, and cleanup evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    operation_id: Annotated[str, Field(strict=True, max_length=MAX_OPERATION_ID_CHARS)]
    archive_path: Annotated[str, Field(strict=True, max_length=MAX_PATH_STRING_CHARS)]
    archive_digest: Annotated[str, Field(strict=True, max_length=MAX_DIGEST_STRING_CHARS)]
    layout_digest: Annotated[str, Field(strict=True, max_length=MAX_DIGEST_STRING_CHARS)]
    codec: CodecKind
    stages: tuple[OperationStage, ...] = Field(default_factory=tuple, max_length=MAX_STAGE_ITEMS)
    preflight: DiskPreflight
    cleanup: CleanupEvidence = Field(default_factory=CleanupEvidence)
    durability_uncertain: Annotated[bool, Field(strict=True)] = False
    completed_at: datetime

    @field_validator("archive_digest", "layout_digest")
    @classmethod
    def validate_digests(cls, value: str) -> str:
        return validate_digest(value)


class ImportResult(BaseModel):
    """Structured import outcome with the registration plan boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    operation_id: Annotated[str, Field(strict=True, max_length=MAX_OPERATION_ID_CHARS)]
    source: Annotated[str, Field(strict=True, max_length=MAX_PATH_STRING_CHARS)]
    archive_digest: Annotated[str, Field(strict=True, max_length=MAX_DIGEST_STRING_CHARS)]
    codec: CodecKind
    registration: RegistrationPlan
    stages: tuple[OperationStage, ...] = Field(default_factory=tuple, max_length=MAX_STAGE_ITEMS)
    cleanup: CleanupEvidence = Field(default_factory=CleanupEvidence)
    completed_at: datetime

    @field_validator("archive_digest")
    @classmethod
    def validate_archive_digest(cls, value: str) -> str:
        return validate_digest(value)


class Deadline:
    """Monotonic deadline for bounded operation time."""

    __slots__ = ("seconds", "_start", "_clock")

    def __init__(
        self,
        seconds: float,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        from time import monotonic

        if (
            type(seconds) not in (int, float)
            or type(seconds) is bool
            or not math.isfinite(float(seconds))
            or seconds <= 0
            or seconds > ABS_MAX_DEADLINE_SECONDS
        ):
            raise ValueError("deadline must be finite and within the hard cap")
        if clock is not None and not callable(clock):
            raise ValueError("deadline clock must be callable")
        self.seconds = seconds
        self._clock = monotonic if clock is None else clock
        self._start = self._validated_clock_read(self._clock)

    def remaining(self) -> float:
        seconds, start, clock = self._snapshot()
        return start + seconds - self._validated_clock_read(clock)

    def _snapshot(self) -> tuple[float, float, Callable[[], float]]:
        seconds = self.seconds
        start = self._start
        clock = self._clock
        if (
            type(seconds) not in (int, float)
            or type(seconds) is bool
            or not math.isfinite(float(seconds))
            or seconds <= 0
            or seconds > ABS_MAX_DEADLINE_SECONDS
        ):
            raise ValueError("deadline state is corrupted")
        if (
            type(start) not in (int, float)
            or type(start) is bool
            or not math.isfinite(float(start))
        ):
            raise ValueError("deadline state is corrupted")
        if not callable(clock):
            raise ValueError("deadline state is corrupted")
        return float(seconds), float(start), clock

    def _validated_clock_read(self, clock: Callable[[], float]) -> float:
        reading = clock()
        if (
            type(reading) not in (int, float)
            or type(reading) is bool
            or not math.isfinite(float(reading))
        ):
            raise ValueError("deadline clock returned a non-finite reading")
        return float(reading)

    def check(self, stage: OperationStage) -> None:
        if self.remaining() <= 0:
            raise LimitExceededError(
                "operation deadline exceeded",
                code="DEADLINE_EXCEEDED",
                stage=stage,
                recovery_action=RecoveryAction.RETRY,
            )


def utc_now() -> datetime:
    return datetime.now(UTC)
