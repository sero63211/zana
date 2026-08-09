"""SQLAlchemy ORM entities matching the ZANA durable schema."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from zana_core.domain.enums import (
    BuildJobStatus,
    InstanceStatus,
    JobEventKind,
    JobKind,
    JobStatus,
    MemoryStatus,
    MessageRole,
    ModelIdentityStrength,
    RuntimeKind,
    RuntimeSource,
    RuntimeStatus,
    VerificationStatus,
)


def now_utc() -> datetime:
    return datetime.now(UTC)


def enum_values(enum_type: type[Enum]) -> list[str]:
    return [member.value for member in enum_type]


def enum_column(enum_type: type[Enum], length: int) -> SAEnum:
    return SAEnum(enum_type, native_enum=False, length=length, values_callable=enum_values)


class Base(DeclarativeBase):
    """Declarative base for ZANA entities."""


class Runtime(Base):
    __tablename__ = "runtimes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[RuntimeKind] = mapped_column(enum_column(RuntimeKind, 24), nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[RuntimeSource] = mapped_column(enum_column(RuntimeSource, 16), nullable=False)
    status: Mapped[RuntimeStatus] = mapped_column(enum_column(RuntimeStatus, 16), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Model(Base):
    __tablename__ = "models"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    runtime_id: Mapped[int] = mapped_column(
        ForeignKey("runtimes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    digest: Mapped[str | None] = mapped_column(Text, nullable=True)
    family: Mapped[str | None] = mapped_column(Text, nullable=True)
    format: Mapped[str | None] = mapped_column(Text, nullable=True)
    quantization: Mapped[str | None] = mapped_column(Text, nullable=True)
    parameter_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    context_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    capabilities_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    identity_strength: Mapped[ModelIdentityStrength] = mapped_column(
        enum_column(ModelIdentityStrength, 32), nullable=False
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Capability(Base):
    __tablename__ = "capabilities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    working_dir: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )


class CapabilitySource(Base):
    __tablename__ = "capability_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    capability_id: Mapped[int] = mapped_column(
        ForeignKey("capabilities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    original_name: Mapped[str] = mapped_column(Text, nullable=False)
    local_path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[JobKind] = mapped_column(enum_column(JobKind, 40), nullable=False)
    status: Mapped[JobStatus] = mapped_column(enum_column(JobStatus, 16), nullable=False)
    progress_0_1: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    phase: Mapped[str] = mapped_column(Text, nullable=False, default="")
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[JobEventKind] = mapped_column(enum_column(JobEventKind, 32), nullable=False)
    phase: Mapped[str] = mapped_column(Text, nullable=False, default="")
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    progress_0_1: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, index=True
    )


class BuildJob(Base):
    __tablename__ = "build_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    capability_id: Mapped[int] = mapped_column(
        ForeignKey("capabilities.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    model_key: Mapped[str] = mapped_column(
        ForeignKey("models.key", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[BuildJobStatus] = mapped_column(
        enum_column(BuildJobStatus, 32), nullable=False, index=True
    )
    policy_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    plan_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    hardware_profile_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    baseline_report_digest: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_report_digest: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_digest: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class Artifact(Base):
    __tablename__ = "artifacts"

    digest: Mapped[str] = mapped_column(Text, primary_key=True)
    media_type: Mapped[str] = mapped_column(Text, nullable=False)
    local_path: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )
    reference_count: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Image(Base):
    __tablename__ = "images"

    digest: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    config_digest: Mapped[str] = mapped_column(Text, nullable=False)
    verification_status: Mapped[VerificationStatus] = mapped_column(
        enum_column(VerificationStatus, 32), nullable=False
    )
    base_model_key: Mapped[str] = mapped_column(Text, nullable=False)
    base_model_digest: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )


class ImageArtifact(Base):
    __tablename__ = "image_artifacts"

    image_digest: Mapped[str] = mapped_column(
        ForeignKey("images.digest", ondelete="CASCADE"), primary_key=True
    )
    artifact_digest: Mapped[str] = mapped_column(
        ForeignKey("artifacts.digest", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(Text, primary_key=True)


class Instance(Base):
    __tablename__ = "instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    image_digest: Mapped[str] = mapped_column(
        ForeignKey("images.digest", ondelete="RESTRICT"), nullable=False, index=True
    )
    runtime_id: Mapped[int | None] = mapped_column(
        ForeignKey("runtimes.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[InstanceStatus] = mapped_column(enum_column(InstanceStatus, 16), nullable=False)
    state_schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_id: Mapped[int] = mapped_column(
        ForeignKey("instances.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[MessageRole] = mapped_column(enum_column(MessageRole, 16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    provenance_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_id: Mapped[int] = mapped_column(
        ForeignKey("instances.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[MemoryStatus] = mapped_column(enum_column(MemoryStatus, 16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )


class StateSnapshot(Base):
    __tablename__ = "state_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_id: Mapped[int] = mapped_column(
        ForeignKey("instances.id", ondelete="CASCADE"), nullable=False, index=True
    )
    image_digest: Mapped[str] = mapped_column(Text, nullable=False)
    state_digest: Mapped[str] = mapped_column(Text, nullable=False)
    local_path: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )
