"""Frozen typed native acquisition models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from zana_core.acquisition.limits import (
    MAX_ERROR_CODE_LENGTH,
    MAX_EVENT_COUNT,
    MAX_MODEL_REFERENCE_BYTES,
    MAX_PROGRESS_VALUE,
    MAX_RETAINED_EVENTS,
    MAX_SEQUENCE,
)
from zana_core.acquisition.redact import sanitize_model_reference

_MAX_TEXT_CHARS = 512
_MAX_REASON_CHARS = 256
_MAX_MESSAGE_CHARS = 256
_MAX_RUNTIME_CHARS = 100
_MAX_INSTRUCTIONS_CHARS = 1024
_MAX_ACTION_CHARS = 128
_MAX_ACTION_ITEMS = 8


def _validate_utf8_bounded(value: str, max_chars: int, max_bytes: int, field: str) -> str:
    if len(value) > max_chars:
        raise ValueError(f"{field} exceeds {max_chars} characters")
    if len(value.encode("utf-8")) > max_bytes:
        raise ValueError(f"{field} exceeds {max_bytes} UTF-8 bytes")
    return value


class AcquisitionKind(str, Enum):
    """Supported native acquisition kinds."""

    OLLAMA_PULL = "ollama_pull"
    UNSUPPORTED = "unsupported"


class AcquisitionState(str, Enum):
    """Deterministic acquisition state machine."""

    PREFLIGHT = "PREFLIGHT"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class AcquisitionPolicy(str, Enum):
    """Endpoint and remote acquisition policy."""

    LOCAL_ONLY = "local_only"
    EXPLICIT_REMOTE_ALLOWED = "explicit_remote_allowed"


class NativeAcquisitionRequest(BaseModel):
    """Validated bounded acquisition request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: AcquisitionKind
    endpoint: str = Field(min_length=1, max_length=2000)
    model_reference: str = Field(min_length=1, max_length=MAX_MODEL_REFERENCE_BYTES)
    policy: AcquisitionPolicy = AcquisitionPolicy.LOCAL_ONLY
    expected_size_bytes: int | None = Field(default=None, ge=0, le=MAX_PROGRESS_VALUE)
    user_approved: bool = False
    deadline_seconds: float = Field(default=30.0, gt=0, le=3600)

    @field_validator("model_reference")
    @classmethod
    def _validate_model_reference(cls, value: str) -> str:
        return sanitize_model_reference(value)

    @field_validator("endpoint")
    @classmethod
    def _validate_endpoint_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 2000:
            raise ValueError("endpoint exceeds 2000 UTF-8 bytes")
        return value


class AdmissionResult(BaseModel):
    """Narrow injected resource admission protocol result."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed: bool
    reason: str = Field(default="", max_length=_MAX_REASON_CHARS)
    conservative_reserve_bytes: int = Field(default=0, ge=0, le=MAX_PROGRESS_VALUE)
    explicit_user_approval: bool = False

    @field_validator("reason")
    @classmethod
    def _reason_bytes(cls, value: str) -> str:
        return _validate_utf8_bounded(value, _MAX_REASON_CHARS, 512, "AdmissionResult.reason")


class OllamaPullBody(BaseModel):
    """Typed frozen native /api/pull body; no arbitrary unbounded dict."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str = Field(min_length=1, max_length=MAX_MODEL_REFERENCE_BYTES)
    stream: Literal[True] = True

    @field_validator("model")
    @classmethod
    def _model_bytes(cls, value: str) -> str:
        return _validate_utf8_bounded(
            value,
            MAX_MODEL_REFERENCE_BYTES,
            MAX_MODEL_REFERENCE_BYTES,
            "OllamaPullBody.model",
        )


class NativeAcquisitionPlan(BaseModel):
    """Prepared native plan; never contains shell strings or secrets."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal[AcquisitionKind.OLLAMA_PULL] = AcquisitionKind.OLLAMA_PULL
    endpoint: str = Field(min_length=1, max_length=2000)
    method: Literal["POST"] = "POST"
    path: Literal["/api/pull"] = "/api/pull"
    model_reference: str = Field(min_length=1, max_length=MAX_MODEL_REFERENCE_BYTES)
    body: OllamaPullBody
    stream: Literal[True] = True
    planned_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("endpoint")
    @classmethod
    def _validate_endpoint_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 2000:
            raise ValueError("endpoint exceeds 2000 UTF-8 bytes")
        return value

    @model_validator(mode="after")
    def _validate_body_matches(self) -> NativeAcquisitionPlan:
        if self.body.model != self.model_reference:
            raise ValueError("plan model_reference must match body model")
        return self

    @field_validator("model_reference")
    @classmethod
    def _model_reference_bytes(cls, value: str) -> str:
        return _validate_utf8_bounded(
            value,
            MAX_MODEL_REFERENCE_BYTES,
            MAX_MODEL_REFERENCE_BYTES,
            "NativeAcquisitionPlan.model_reference",
        )


class NativeAcquisitionProgress(BaseModel):
    """One bounded progress event; retained list is capped by policy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sequence: int = Field(ge=0, le=MAX_SEQUENCE)
    status: str = Field(min_length=1, max_length=_MAX_TEXT_CHARS)
    digest: str | None = Field(default=None, max_length=_MAX_TEXT_CHARS)
    total: int | None = Field(default=None, ge=0, le=MAX_PROGRESS_VALUE)
    completed: int | None = Field(default=None, ge=0, le=MAX_PROGRESS_VALUE)
    progress_0_1: float | None = Field(default=None, ge=0, le=1)
    error: str | None = Field(default=None, max_length=_MAX_TEXT_CHARS)
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _validate_completed_within_total(self) -> NativeAcquisitionProgress:
        if self.total is not None and self.completed is not None and self.completed > self.total:
            raise ValueError("completed must not exceed total")
        return self

    @field_validator("status")
    @classmethod
    def _status_bytes(cls, value: str) -> str:
        return _validate_utf8_bounded(
            value, _MAX_TEXT_CHARS, 1024, "NativeAcquisitionProgress.status"
        )

    @field_validator("digest")
    @classmethod
    def _digest_bytes(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_utf8_bounded(
            value, _MAX_TEXT_CHARS, 1024, "NativeAcquisitionProgress.digest"
        )

    @field_validator("error")
    @classmethod
    def _error_bytes(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_utf8_bounded(
            value, _MAX_TEXT_CHARS, 1024, "NativeAcquisitionProgress.error"
        )


class NativeAcquisitionResult(BaseModel):
    """Deterministic terminal result."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request: NativeAcquisitionRequest
    state: AcquisitionState
    events_consumed: int = Field(ge=0, le=MAX_EVENT_COUNT)
    retained_events: list[NativeAcquisitionProgress] = Field(
        default_factory=list, max_length=MAX_RETAINED_EVENTS
    )
    error_code: str | None = Field(default=None, max_length=MAX_ERROR_CODE_LENGTH)
    error_message: str | None = Field(default=None, max_length=_MAX_MESSAGE_CHARS)
    completed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("error_code")
    @classmethod
    def _error_code_bytes(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_utf8_bounded(
            value, MAX_ERROR_CODE_LENGTH, 128, "NativeAcquisitionResult.error_code"
        )

    @field_validator("error_message")
    @classmethod
    def _error_message_bytes(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_utf8_bounded(
            value, _MAX_MESSAGE_CHARS, 512, "NativeAcquisitionResult.error_message"
        )


class UnsupportedRuntimeResult(BaseModel):
    """Actionable native instructions for unsupported runtimes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    runtime: str = Field(min_length=1, max_length=_MAX_RUNTIME_CHARS)
    message: str = Field(max_length=_MAX_MESSAGE_CHARS)
    native_instructions: str = Field(max_length=_MAX_INSTRUCTIONS_CHARS)
    actions: list[str] = Field(default_factory=list, max_length=_MAX_ACTION_ITEMS)

    @field_validator("runtime")
    @classmethod
    def _runtime_bytes(cls, value: str) -> str:
        return _validate_utf8_bounded(
            value, _MAX_RUNTIME_CHARS, 200, "UnsupportedRuntimeResult.runtime"
        )

    @field_validator("message")
    @classmethod
    def _message_bytes(cls, value: str) -> str:
        return _validate_utf8_bounded(
            value, _MAX_MESSAGE_CHARS, 512, "UnsupportedRuntimeResult.message"
        )

    @field_validator("native_instructions")
    @classmethod
    def _instructions_bytes(cls, value: str) -> str:
        return _validate_utf8_bounded(
            value, _MAX_INSTRUCTIONS_CHARS, 2048, "UnsupportedRuntimeResult.native_instructions"
        )

    @field_validator("actions")
    @classmethod
    def _actions_bytes(cls, value: list[str]) -> list[str]:
        if len(value) > _MAX_ACTION_ITEMS:
            raise ValueError("actions exceeds the item cap")
        for item in value:
            _validate_utf8_bounded(item, _MAX_ACTION_CHARS, 256, "UnsupportedRuntimeResult.action")
        return value


def unsupported_runtime_result(runtime: str) -> UnsupportedRuntimeResult:
    return UnsupportedRuntimeResult(
        runtime=runtime,
        message=f"Native acquisition is not supported for {runtime}.",
        native_instructions=(
            "Use the runtime's own download or model-management UI, then refresh discovery in ZANA."
        ),
        actions=["open_runtime_ui", "refresh_discovery"],
    )
