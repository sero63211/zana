"""Strict immutable tool boundary models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class ToolDefinition(BaseModel):
    """Immutable description of one trusted code-owned tool."""

    model_config = ConfigDict(extra="forbid")

    id: str
    version: str
    description: str
    input_schema: dict[str, Any]


class ToolCall(BaseModel):
    """One validated tool invocation request."""

    model_config = ConfigDict(extra="forbid")

    call_id: str
    tool_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    image_digest: str | None = None
    instance_id: str | None = None


class ToolStatus(str, Enum):
    """Tool execution status."""

    SUCCESS = "success"
    ERROR = "error"


class ToolErrorCode(str, Enum):
    """Recovery-oriented tool error codes."""

    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    MALFORMED_INPUT = "MALFORMED_INPUT"
    CALCULATION_ERROR = "CALCULATION_ERROR"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ToolError(BaseModel):
    """Structured recovery-oriented tool error."""

    model_config = ConfigDict(extra="forbid")

    code: ToolErrorCode
    message: str
    recoverable: bool = True


class ToolResult(BaseModel):
    """Deterministic typed tool output."""

    model_config = ConfigDict(extra="forbid")

    call_id: str
    tool_id: str
    status: ToolStatus
    output: dict[str, Any] = Field(default_factory=dict)
    error: ToolError | None = None


class ToolExecutionProvenance(BaseModel):
    """Redacted execution provenance; never logs secrets or full prompts."""

    model_config = ConfigDict(extra="forbid")

    call_id: str
    tool_id: str
    tool_version: str
    permission_decision: str
    input_digest: str
    status: ToolStatus
    result_digest: str | None = None
    error_code: str | None = None
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    duration_ms: int | None = None
    image_digest: str | None = None
    instance_id: str | None = None
