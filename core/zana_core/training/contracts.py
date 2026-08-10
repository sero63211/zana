"""Strict immutable typed contracts for the ZANA training foundation."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import Enum
from math import isfinite
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


_SHA256_RE = re.compile(r"^(?:sha256:)?[0-9a-fA-F]{64}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

MAX_RECORD_IDS = 100_000
MAX_RECORD_ID_CHARS = 256
MAX_SOURCE_ID_CHARS = 128
MAX_ITERS = 100_000
MAX_BATCH_SIZE = 1024
MAX_LEARNING_RATE = 1.0
MAX_SEQ_LENGTH = 131_072
MAX_NUM_LAYERS = 128
MAX_DATASET_FILE_BYTES = 256 * 1024 * 1024
MAX_ADAPTER_BYTES = 512 * 1024 * 1024
MAX_LOG_BYTES = 4 * 1024 * 1024
MAX_LOG_DIAGNOSTIC_CHARS = 64 * 1024
MAX_SOURCE_FILE_CAP = 64 * 1024 * 1024
MAX_ADAPTER_CAP = 512 * 1024 * 1024
MAX_LOG_CAP = 4 * 1024 * 1024
MAX_DEADLINE_SECONDS = 7 * 24 * 60 * 60
MAX_TERMINATE_GRACE_SECONDS = 300.0


def normalize_sha256(value: str) -> str:
    """Normalize ``hex`` or ``sha256:hex`` to lowercase hex, rejecting anything else."""
    if _SHA256_RE.fullmatch(value) is None:
        raise ValueError("expected a SHA-256 digest in hex or sha256:hex form")
    return value.removeprefix("sha256:").lower()


def validate_finite_positive(value: float, name: str, maximum: float) -> float:
    """Validate a finite, positive, bounded numeric limit."""
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    if not isinstance(value, int | float) or not isfinite(float(value)):
        raise ValueError(f"{name} must be a finite number")
    if value <= 0 or value > maximum:
        raise ValueError(f"{name} must be positive and at most {maximum}")
    return float(value)


class InferenceIdentity(BaseModel):
    """Runtime-visible model identity used only for inference, never training."""

    model_config = ConfigDict(extra="forbid")

    runtime_id: str
    model_id: str
    display_name: str
    digest: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("digest")
    @classmethod
    def digest_must_be_exact(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_sha256(value)


class TrainingSourceIdentity(BaseModel):
    """Exact trainable source identity that can feed a provider."""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    digest: str
    format: str
    provider: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("digest")
    @classmethod
    def digest_must_be_exact(cls, value: str) -> str:
        return normalize_sha256(value)


class AdapterBaseIdentity(BaseModel):
    """Exact base identity an adapter is bound to; display names are never enough."""

    model_config = ConfigDict(extra="forbid")

    base_model_digest: str
    training_source_digest: str
    training_source_provider: str
    provider_version: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("base_model_digest", "training_source_digest")
    @classmethod
    def digest_must_be_exact(cls, value: str) -> str:
        return normalize_sha256(value)

    @field_validator("training_source_provider")
    @classmethod
    def provider_must_be_mlx(cls, value: str) -> str:
        if value != "mlx_lm":
            raise ValueError("only mlx_lm is trainable in ZANA v1")
        return value

    @field_validator("provider_version")
    @classmethod
    def provider_version_must_be_exact(cls, value: str) -> str:
        if _VERSION_RE.fullmatch(value) is None:
            raise ValueError("provider version must be a bounded exact version string")
        return value


class LocalTrainingSource(BaseModel):
    """Explicitly acquired local trainable model source, kept apart from digests.

    A digest identifies a base model; it is never a filesystem path. Only an
    absolute, approved, locally acquired directory may be passed to a provider.
    """

    model_config = ConfigDict(extra="forbid")

    source_id: str
    path: Path
    digest: str
    format: str = "mlx"
    provider: str = "mlx_lm"
    acquired_locally: bool = True
    approved: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_id")
    @classmethod
    def source_id_must_be_bounded(cls, value: str) -> str:
        if _SAFE_ID_RE.fullmatch(value) is None or len(value) > MAX_SOURCE_ID_CHARS:
            raise ValueError("source id must be a bounded safe identifier")
        return value

    @field_validator("path")
    @classmethod
    def path_must_be_absolute(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("training source path must be an absolute local directory")
        return value

    @field_validator("digest")
    @classmethod
    def digest_must_be_exact(cls, value: str) -> str:
        return normalize_sha256(value)

    @field_validator("provider")
    @classmethod
    def provider_must_be_mlx(cls, value: str) -> str:
        if value != "mlx_lm":
            raise ValueError("only mlx_lm is trainable in ZANA v1")
        return value

    @field_validator("acquired_locally", "approved")
    @classmethod
    def must_be_explicitly_acquired(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("training source must be locally acquired and approved")
        return value


class DatasetSplitManifest(BaseModel):
    """Immutable dataset split manifest with canonical file hashes."""

    model_config = ConfigDict(extra="forbid")

    role: str
    path: Path
    sha256: str
    size_bytes: int
    record_ids: tuple[str, ...] = ()

    @field_validator("path")
    @classmethod
    def path_must_be_absolute(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("dataset split path must be an absolute approved local path")
        return value

    @field_validator("sha256")
    @classmethod
    def sha256_must_be_exact(cls, value: str) -> str:
        return normalize_sha256(value)

    @field_validator("size_bytes", mode="before")
    @classmethod
    def size_must_be_bounded(cls, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("dataset split size must be a strict integer")
        if value < 0 or value > MAX_DATASET_FILE_BYTES:
            raise ValueError("dataset split size is out of bounds")
        return value

    @field_validator("record_ids")
    @classmethod
    def record_ids_must_be_bounded(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > MAX_RECORD_IDS:
            raise ValueError(f"record_ids exceed the {MAX_RECORD_IDS} bound")
        if any(not record_id or len(record_id) > MAX_RECORD_ID_CHARS for record_id in value):
            raise ValueError("record ids must be non-empty and bounded in length")
        if len(set(value)) != len(value):
            raise ValueError("record ids must be unique within a split")
        return value


class ProviderProbeStatus(str, Enum):
    """Metadata-only provider probe outcome."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class ProviderProbe(BaseModel):
    """Metadata-only provider probe result; never starts a provider."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    status: ProviderProbeStatus
    version: str | None = None
    platform_ok: bool = False
    evidence: list[str] = Field(default_factory=list)
    error: str | None = None


class CompatibilityDecision(str, Enum):
    """Whether exact base identity and provider compatibility are proven."""

    TRAINABLE = "trainable"
    NOT_TRAINABLE = "not_trainable"
    UNKNOWN = "unknown"


class TrainingRequestConfig(BaseModel):
    """User-reviewed training request configuration."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    source: LocalTrainingSource
    base: AdapterBaseIdentity
    train_split: DatasetSplitManifest
    validation_split: DatasetSplitManifest | None = None
    eval_split: DatasetSplitManifest | None = None
    seed: int
    iters: int = Field(gt=0, le=MAX_ITERS)
    batch_size: int = Field(default=1, gt=0, le=MAX_BATCH_SIZE)
    learning_rate: float | None = Field(default=None, gt=0, le=MAX_LEARNING_RATE)
    max_seq_length: int | None = Field(default=None, gt=0, le=MAX_SEQ_LENGTH)
    num_layers: int | None = Field(default=None, gt=0, le=MAX_NUM_LAYERS)
    grad_checkpoint: bool = False
    dry_run_required: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("provider")
    @classmethod
    def provider_must_be_mlx(cls, value: str) -> str:
        if value != "mlx_lm":
            raise ValueError("only mlx_lm is trainable in ZANA v1")
        return value

    @field_validator("seed", mode="before")
    @classmethod
    def seed_must_be_bounded(cls, value: int, info: ValidationInfo) -> int:
        if isinstance(value, bool):
            raise ValueError("seed must be a strict integer")
        if not isinstance(value, int):
            raise ValueError("seed must be a strict integer")
        if value < 0 or value >= 2**31:
            raise ValueError("seed must be a bounded non-negative integer")
        return value

    @field_validator("iters", "batch_size", "max_seq_length", "num_layers", mode="before")
    @classmethod
    def strict_bounded_int(cls, value: Any, info: ValidationInfo) -> Any:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{info.field_name} must be a strict integer")
        if value <= 0:
            raise ValueError(f"{info.field_name} must be positive")
        return value

    @field_validator("learning_rate", mode="before")
    @classmethod
    def learning_rate_must_be_finite(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError("learning_rate must be a finite number")
        if not isfinite(float(value)):
            raise ValueError("learning_rate must be finite")
        if float(value) <= 0:
            raise ValueError("learning_rate must be positive")
        return float(value)


class ResourceGuardDecision(str, Enum):
    """Explicit resource guard outcome; never guesses success."""

    ALLOW = "allow"
    BLOCK = "block"
    UNKNOWN = "unknown"


class ResourceGuard(BaseModel):
    """Resource guard result with explicit allow/block/unknown."""

    model_config = ConfigDict(extra="forbid")

    resource: str
    decision: ResourceGuardDecision
    available: int | float | None = None
    required: int | float | None = None
    reason: str


class InvocationSpec(BaseModel):
    """Allowlisted command specification consumed only by the executor boundary."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    executable: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = Field(default_factory=dict)
    provider_version: str
    package_version: str
    seed: int
    dataset_digest: str
    config_digest: str
    output_path: Path
    environment_metadata: dict[str, str] = Field(default_factory=dict)


class AdapterState(str, Enum):
    """Adapter artifact lifecycle; partial adapters are never usable."""

    PARTIAL = "partial"
    COMPLETE = "complete"
    REJECTED = "rejected"


class CancellationState(str, Enum):
    """Cancellation/partial-artifact state machine."""

    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    FAILED = "failed"
    COMPLETED = "completed"


class AdapterMetadata(BaseModel):
    """Canonical adapter artifact metadata."""

    model_config = ConfigDict(extra="forbid")

    type: str = "lora"
    format: str = "safetensors"
    base_model_digest: str
    training_provider: str
    training_provider_version: str
    dataset_digest: str
    config_digest: str
    adapter_digest: str
    seed: int
    package_version: str | None = None
    state: AdapterState = AdapterState.COMPLETE


class AdapterMaterializationCompatibility(BaseModel):
    """Runtime materialization compatibility decision; no runtime contact."""

    model_config = ConfigDict(extra="forbid")

    runtime_id: str
    compatible: bool
    reason: str
    adapter: AdapterMetadata | None = None


class SyntheticDataset(BaseModel):
    """Deterministic synthetic dataset definition with disjoint held-out range."""

    model_config = ConfigDict(extra="forbid")

    generator_identity: str
    seed: int
    label_verifier: str
    held_out_seed: int
    held_out_range: tuple[int, int]


class RunRecord(BaseModel):
    """Immutable run record preserving cancellation/partial-artifact state."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    provider: str
    invocation: InvocationSpec
    started_at: datetime = Field(default_factory=utc_now)
    state: CancellationState = CancellationState.RUNNING
    adapter: AdapterMetadata | None = None
    log_path: Path | None = None
    partial_outputs: tuple[Path, ...] = ()
    error: str | None = None


class TrainingState(str, Enum):
    """Training lifecycle state machine."""

    PENDING = "pending"
    RUNNING = "running"
    CANCELLED = "cancelled"
    FAILED = "failed"
    COMPLETED = "completed"


class ExecutionStatus(str, Enum):
    """Outcome of a bounded training execution attempt."""

    NOT_STARTED = "not_started"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class ExecutionResult(BaseModel):
    """Sanitized result of a training execution; partial output is never promoted."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: ExecutionStatus
    exit_code: int | None = None
    cancelled: bool = False
    timed_out: bool = False
    terminated: bool = False
    killed: bool = False
    adapter: AdapterMetadata | None = None
    adapter_ok: bool = False
    adapter_reason: str | None = None
    adapter_digest: str | None = None
    adapter_path: str | None = None
    log_stdout: str = ""
    log_stderr: str = ""
    error: str | None = None
    blocked_resources: tuple[ResourceGuard, ...] = ()

    @field_validator("adapter_path")
    @classmethod
    def adapter_path_must_be_relative(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value.startswith("/") or value.startswith("\\") or ".." in value.split("/"):
            raise ValueError("adapter path must be relative to the private workspace")
        return value

    @field_validator("log_stdout", "log_stderr")
    @classmethod
    def logs_must_be_bounded(cls, value: str) -> str:
        if len(value) > MAX_LOG_DIAGNOSTIC_CHARS:
            raise ValueError("log diagnostics exceed the bounded limit")
        if len(value.encode("utf-8", errors="replace")) > MAX_LOG_DIAGNOSTIC_CHARS:
            raise ValueError("log diagnostics exceed the bounded UTF-8 limit")
        return value
