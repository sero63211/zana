"""Typed API request/response models for the ZANA backend contract."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from zana_core.domain.enums import (
    BuildJobStatus,
    InstanceStatus,
    JobEventKind,
    JobKind,
    JobStatus,
    ModelIdentityStrength,
    RuntimeKind,
    RuntimeSource,
    RuntimeStatus,
    VerificationStatus,
)


class RuntimeCreate(BaseModel):
    kind: RuntimeKind
    endpoint: str = Field(min_length=1, max_length=2000)
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class RuntimeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: RuntimeKind
    endpoint: str
    source: RuntimeSource
    status: RuntimeStatus
    metadata_json: dict[str, Any]
    last_seen_at: datetime | None


class ModelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    runtime_id: int
    model_id: str
    digest: str | None
    family: str | None
    format: str | None
    quantization: str | None
    parameter_count: int | None
    size_bytes: int | None
    context_length: int | None
    capabilities_json: list[str]
    identity_strength: ModelIdentityStrength
    metadata_json: dict[str, Any]
    last_seen_at: datetime | None


class CapabilityCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=300)
    version: str = Field(min_length=1, max_length=100)
    manifest_json: dict[str, Any] = Field(default_factory=dict)


class CapabilityUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str | None = Field(default=None, min_length=1, max_length=300)
    version: str | None = Field(default=None, min_length=1, max_length=100)
    manifest_json: dict[str, Any] | None = None


class CapabilityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    version: str
    manifest_json: dict[str, Any]
    working_dir: str
    created_at: datetime
    updated_at: datetime


class AnalyzeBuildRequest(BaseModel):
    capability_id: int
    model_key: str = Field(min_length=1, max_length=255)
    policy_json: dict[str, Any] = Field(default_factory=dict)


class BuildJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    capability_id: int
    model_key: str
    status: BuildJobStatus
    policy_json: dict[str, Any]
    plan_json: dict[str, Any] | None
    hardware_profile_json: dict[str, Any] | None
    baseline_report_digest: str | None
    candidate_report_digest: str | None
    image_digest: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error_json: dict[str, Any] | None


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: JobKind
    status: JobStatus
    progress_0_1: float
    phase: str
    message: str
    error_json: dict[str, Any] | None


class JobEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    kind: JobEventKind
    phase: str
    message: str
    progress_0_1: float
    error_json: dict[str, Any] | None
    created_at: datetime


class ImageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    digest: str
    name: str
    version: str
    config_digest: str
    verification_status: VerificationStatus
    base_model_key: str
    base_model_digest: str
    created_at: datetime


class InstanceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    image_digest: str
    runtime_id: int | None
    status: InstanceStatus
    state_schema_version: int
    created_at: datetime
    updated_at: datetime


class SystemDoctorRead(BaseModel):
    status: str
    sqlite_version: str
    journal_mode: str
    foreign_keys: bool
    busy_timeout_ms: int
    migration_revision: str | None
    db_path: str
    table_counts: dict[str, int]
    core_pid: int
    core_uptime_seconds: float


class ModelPullCreate(BaseModel):
    """Typed request to record a runtime-native model acquisition job."""

    model_config = ConfigDict(extra="forbid", strict=True)

    runtime_id: int = Field(gt=0)
    model_reference: str = Field(min_length=1, max_length=200)
    expected_size_bytes: int | None = Field(default=None, ge=0, le=1 << 40)
    user_approved: bool
    deadline_seconds: float = Field(default=30.0, gt=0, le=3600)


class CapabilityBehaviorSourceCreate(BaseModel):
    """Bounded UTF-8 behavior/system content for the canonical system file."""

    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["behavior"]
    content: str = Field(min_length=1, max_length=1_000_000)


class CapabilityDocumentSourceCreate(BaseModel):
    """Explicitly approved local PDF/Markdown/TXT document copy."""

    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["document"]
    local_path: str = Field(min_length=1, max_length=2000)
    user_approved: bool = False


class CapabilityEvaluationSourceCreate(BaseModel):
    """Bounded UTF-8 domain/regression evaluation JSONL."""

    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["evaluation"]
    eval_kind: Literal["domain", "regression"]
    content: str = Field(min_length=1, max_length=4_000_000)


CapabilitySourceCreate = Annotated[
    CapabilityBehaviorSourceCreate
    | CapabilityDocumentSourceCreate
    | CapabilityEvaluationSourceCreate,
    Field(discriminator="kind"),
]


class CapabilitySourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: int
    capability_id: int
    original_name: str
    local_path: str
    sha256: str
    media_type: str
    size_bytes: int
    metadata_json: dict[str, Any]


class CapabilityIssueRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    file: str | None = None
    line: int | None = None


class CapabilityProvenanceRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relative_path: str
    sha256: str
    size_bytes: int
    role: str
    title: str
    title_origin: str
    declared_license: str | None
    usage_metadata: dict[str, Any]
    ingested_at: datetime
    rights_inferred: bool


class CapabilityValidationRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_id: int
    root_relative: str
    manifest_present: bool
    valid: bool
    issue_count: int
    returned_issue_count: int
    issues: list[CapabilityIssueRead]
    provenance: list[CapabilityProvenanceRead]
    validated_at: datetime


class CapabilityDetailRead(BaseModel):
    """Typed detail without full host paths or document contents."""

    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    version: str
    manifest_json: dict[str, Any]
    workspace_relative: str
    sources: list[CapabilitySourceRead]
    created_at: datetime
    updated_at: datetime
