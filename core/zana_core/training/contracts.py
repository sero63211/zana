"""Strict immutable typed contracts for the ZANA training foundation."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class InferenceIdentity(BaseModel):
    """Runtime-visible model identity used only for inference, never training."""

    model_config = ConfigDict(extra="forbid")

    runtime_id: str
    model_id: str
    display_name: str
    digest: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TrainingSourceIdentity(BaseModel):
    """Exact trainable source identity that can feed a provider."""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    digest: str
    format: str
    provider: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class AdapterBaseIdentity(BaseModel):
    """Exact base identity an adapter is bound to; display names are never enough."""

    model_config = ConfigDict(extra="forbid")

    base_model_digest: str
    training_source_digest: str
    training_source_provider: str
    provider_version: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class DatasetSplitManifest(BaseModel):
    """Immutable dataset split manifest with canonical file hashes."""

    model_config = ConfigDict(extra="forbid")

    role: str
    path: Path
    sha256: str
    size_bytes: int
    record_ids: tuple[str, ...] = ()


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
    base: AdapterBaseIdentity
    train_split: DatasetSplitManifest
    validation_split: DatasetSplitManifest | None = None
    eval_split: DatasetSplitManifest | None = None
    seed: int
    max_tokens: int | None = Field(default=None, gt=0)
    max_steps: int | None = Field(default=None, gt=0)
    learning_rate: float | None = Field(default=None, gt=0)
    output_path: Path
    dry_run_required: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


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
    """Command specification as data; this task never executes it."""

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
