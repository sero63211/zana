"""Strict immutable instance, session, and chat contract models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from zana_core.domain.enums import InstanceStatus
from zana_core.instances.errors import InstanceErrorRecord
from zana_core.memory.models import InstancePointer, MutableInstanceState
from zana_core.permissions.decisions import Denial


def utc_now() -> datetime:
    return datetime.now(UTC)


class SessionStatus(str, Enum):
    """Runtime session lifecycle status exposed by adapters."""

    NEW = "new"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


class ChatStatus(str, Enum):
    """Typed chat outcome; partial output is never presented as final."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


class GenerationSettings(BaseModel):
    """Deterministic generation settings recorded in provenance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1024, gt=0)
    top_p: float = Field(default=1.0, ge=0.0, le=1.0)
    stop: tuple[str, ...] = Field(default_factory=tuple)


class LowResourceLimits(BaseModel):
    """Bounded counts/sizes for every injected boundary in this lane."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_message_chars: int = Field(default=64_000, gt=0)
    max_user_instructions_chars: int = Field(default=16_000, gt=0)
    max_retrieved_chunks: int = Field(default=16, gt=0)
    max_retrieved_text_chars: int = Field(default=64_000, gt=0)
    max_memory_records: int = Field(default=200, gt=0)
    max_conversation_turns: int = Field(default=500, gt=0)
    max_tool_requests: int = Field(default=16, gt=0)
    max_memory_suggestions: int = Field(default=16, gt=0)
    max_tool_arguments_chars: int = Field(default=16_000, gt=0)
    max_context_chars: int = Field(default=256_000, gt=0)
    max_generation_timeout_seconds: float = Field(default=300.0, gt=0.0)


class InstanceConfig(BaseModel):
    """Immutable configuration bound to one exact image digest.

    The mutable half (``InstancePointer``/``MutableInstanceState``) is stored
    separately on ``InstanceRecord``; image content is never copied.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    instance_name: str = Field(min_length=1, max_length=300)
    image_digest: str = Field(min_length=1)
    image_name: str = Field(min_length=1, max_length=300)
    image_version: str = Field(min_length=1, max_length=100)
    base_model_digest: str = Field(min_length=1)
    required_runtime_compatibility: tuple[str, ...] = Field(default_factory=tuple)
    required_capabilities: tuple[str, ...] = Field(default_factory=tuple)
    required_artifact_digests: tuple[str, ...] = Field(default_factory=tuple)
    required_secret_references: tuple[str, ...] = Field(default_factory=tuple)
    knowledge_snapshot_digest: str | None = None
    tool_ids: tuple[str, ...] = Field(default_factory=tuple)
    context_token_budget: int = Field(default=4096, gt=0)
    low_resource_limits: LowResourceLimits = Field(default_factory=LowResourceLimits)
    generation_settings: GenerationSettings = Field(default_factory=GenerationSettings)


class StartPlan(BaseModel):
    """Fail-closed start preconditions resolved before any runtime call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    instance_id: str = Field(min_length=1)
    image_digest: str = Field(min_length=1)
    base_model_digest: str = Field(min_length=1)
    runtime_id: str = Field(min_length=1)
    runtime_endpoint: str = Field(min_length=1)
    model_key: str = Field(min_length=1)
    model_digest: str = Field(min_length=1)
    expected_state_revision: int = Field(ge=0)
    required_artifact_digests: tuple[str, ...] = Field(default_factory=tuple)
    required_secret_references: tuple[str, ...] = Field(default_factory=tuple)


class SessionBinding(BaseModel):
    """Exact runtime/session/model/image binding returned by an adapter."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str = Field(min_length=1)
    instance_id: str = Field(min_length=1)
    image_digest: str = Field(min_length=1)
    base_model_digest: str = Field(min_length=1)
    runtime_id: str = Field(min_length=1)
    runtime_endpoint: str = Field(min_length=1)
    model_key: str = Field(min_length=1)
    model_digest: str = Field(min_length=1)
    bound_at: datetime = Field(default_factory=utc_now)


class InstanceRecord(BaseModel):
    """One mutable instance: immutable config plus mutable pointer/state."""

    model_config = ConfigDict(extra="forbid")

    instance_id: str = Field(min_length=1)
    config: InstanceConfig
    pointer: InstancePointer
    state: MutableInstanceState
    status: InstanceStatus = InstanceStatus.STOPPED
    binding: SessionBinding | None = None
    last_error: InstanceErrorRecord | None = None
    updated_at: datetime = Field(default_factory=utc_now)


class RetrievedChunk(BaseModel):
    """One retrieval result with stable source locator and score."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chunk_id: str = Field(min_length=1)
    document_digest: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_locator: str = Field(min_length=1)
    score: float = Field(ge=0.0, le=1.0)
    text: str


class ToolRequest(BaseModel):
    """A model-requested built-in tool invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_id: str = Field(min_length=1, max_length=200)
    version: int = Field(default=1, ge=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolDecisionRecord(BaseModel):
    """Structured gate outcome recorded before any execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_id: str
    allowed: bool
    denial: Denial | None = None


class ToolResult(BaseModel):
    """Executed built-in tool output; never carries secret values."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_id: str
    ok: bool
    output: str | None = None
    error: str | None = None
    input_digest: str | None = None
    output_digest: str | None = None


class TruncationRecord(BaseModel):
    """One deterministic context truncation decision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    section: str
    item_id: str
    reason: str = "token_budget_exceeded"
    tokens_saved: int


class ResponseProvenance(BaseModel):
    """Complete chat provenance for one assistant response."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    image_digest: str
    image_name: str
    image_version: str
    base_model_digest: str
    runtime_id: str
    runtime_endpoint: str
    model_key: str
    model_digest: str
    session_id: str
    retrieved_chunks: tuple[RetrievedChunk, ...] = Field(default_factory=tuple)
    tool_decisions: tuple[ToolDecisionRecord, ...] = Field(default_factory=tuple)
    tool_results: tuple[ToolResult, ...] = Field(default_factory=tuple)
    truncation_decisions: tuple[TruncationRecord, ...] = Field(default_factory=tuple)
    memory_ids: tuple[str, ...] = Field(default_factory=tuple)
    generation_settings: GenerationSettings
    evidence_untrusted: bool = True
    raw_output: str | None = None
    started_at: datetime
    finished_at: datetime
    elapsed_seconds: float = Field(ge=0.0)


class ChatInput(BaseModel):
    """User chat input with explicit timeout and generation settings."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    instance_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    conversation_id: str | None = None
    user_instructions: str | None = None
    timeout_seconds: float | None = Field(default=None, gt=0.0)
    generation_settings: GenerationSettings | None = None


class ChatError(BaseModel):
    """Typed chat failure with an explicit recovery action."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    message: str
    recovery_action: str
    recoverable: bool = True


class ChatOutput(BaseModel):
    """Chat result; partial/failed outputs never carry verified content."""

    model_config = ConfigDict(extra="forbid")

    instance_id: str
    status: ChatStatus
    content: str | None = None
    partial: bool = False
    error: ChatError | None = None
    provenance: ResponseProvenance | None = None
    memory_proposals: tuple[str, ...] = Field(default_factory=tuple)


class MemorySuggestion(BaseModel):
    """Model-suggested memory; stored as a pending proposal by default."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    category: Literal["fact", "preference"]
    memory_type: Literal["fact", "preference"]
    content: str = Field(min_length=1)
