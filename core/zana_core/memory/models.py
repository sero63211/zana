"""Typed memory, instance pointer, and mutable state models (pure domain)."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from zana_core.domain.enums import MemoryStatus, MessageRole


class MemoryType(str, Enum):
    """V1 durable memory types from the ZANA memory specification."""

    FACT = "fact"
    PREFERENCE = "preference"


class MemoryCategory(str, Enum):
    """Narrow auto-memory categories; V1 never auto-ingests arbitrary content."""

    FACT = "fact"
    PREFERENCE = "preference"


class ApprovalDecision(str, Enum):
    """Explicit human or policy decision on a memory proposal."""

    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalSource(str, Enum):
    """Who approved or rejected; auto approvals are always user pre-enabled."""

    USER_EXPLICIT = "user_explicit"
    AUTO_MEMORY_POLICY = "auto_memory_policy"


class ApprovalProvenance(BaseModel):
    """Auditable decision metadata; never carries secret or credential material."""

    model_config = ConfigDict(extra="forbid")

    decision: ApprovalDecision
    source: ApprovalSource
    decided_at: datetime
    reason: str = ""
    source_message_id: str | None = None


class ConversationTurn(BaseModel):
    """One typed message in instance conversation history."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    role: MessageRole
    content: str
    source_message_id: str | None = None
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryProposal(BaseModel):
    """A model/user proposal that must be approved before it becomes memory.

    ``status`` maps to the shared ``MemoryStatus`` contract: PENDING,
    APPROVED, or REJECTED. Only APPROVED records are ever active memory.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    instance_id: str = Field(min_length=1)
    type: MemoryType
    category: MemoryCategory
    content: str
    source_message_id: str | None = None
    proposed_at: datetime
    status: MemoryStatus = MemoryStatus.PENDING
    provenance: ApprovalProvenance | None = None


class MemoryRecord(BaseModel):
    """An approved memory fact or instance preference."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    instance_id: str = Field(min_length=1)
    type: MemoryType
    category: MemoryCategory
    content: str
    source_message_id: str | None = None
    created_at: datetime
    status: MemoryStatus = MemoryStatus.APPROVED
    provenance: ApprovalProvenance


class ImagePointer(BaseModel):
    """Immutable reference to a ZANA image by content digest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    digest: str = Field(min_length=1)
    schema_version: int = 1


class InstancePointer(BaseModel):
    """Mutable instance identity: active image pointer plus snapshot revision."""

    model_config = ConfigDict(extra="forbid")

    instance_id: str = Field(min_length=1)
    image: ImagePointer
    snapshot_revision: int = 0
    state_schema_version: int = 1
    updated_at: datetime


class MutableInstanceState(BaseModel):
    """The mutable half of instance state; image content is never duplicated."""

    model_config = ConfigDict(extra="forbid")

    instance_id: str = Field(min_length=1)
    state_revision: int = 0
    conversation: list[ConversationTurn] = Field(default_factory=list)
    approved_facts: list[MemoryRecord] = Field(default_factory=list)
    approved_preferences: list[MemoryRecord] = Field(default_factory=list)
    updated_at: datetime


class InstanceSnapshot(BaseModel):
    """Captured mutable state plus the image pointer at a migration boundary."""

    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = Field(min_length=1)
    instance_id: str = Field(min_length=1)
    image: ImagePointer
    state: MutableInstanceState
    captured_at: datetime
    reason: str = ""


def utc_now() -> datetime:
    """Deterministic UTC now for model defaults."""
    return datetime.now(UTC)
