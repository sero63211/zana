"""Canonical probe contracts shared by all runtime adapters."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from zana_core.domain.enums import (
    ModelIdentityStrength,
    RuntimeKind,
    RuntimeSource,
    RuntimeStatus,
)


class RuntimeProbeError(Exception):
    """Base error for runtime probing failures."""


class RuntimeProbeTimeoutError(RuntimeProbeError):
    """Raised when a runtime probe exceeds its bounded timeout."""


class InvalidRuntimeResponseError(RuntimeProbeError):
    """Raised when a runtime returns a response that cannot be trusted."""


class ManualEndpointError(RuntimeProbeError):
    """Raised when a manually supplied runtime endpoint is invalid."""

    def __init__(self, message: str, *, code: str, actions: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.code = code
        self.actions = tuple(actions)


class AdapterType(str, Enum):
    """Adapter selection for manual endpoints and registry targets."""

    AUTO = "auto"
    OLLAMA = "ollama"
    OPENAI_COMPATIBLE = "openai-compatible"
    LM_STUDIO = "lm-studio"
    LLAMA_CPP = "llama.cpp"
    MLX_LM = "mlx-lm"


class ModelDescriptor(BaseModel):
    """Canonical model descriptor matching the discovery specification."""

    model_config = ConfigDict(frozen=True)

    runtime_id: str
    model_id: str
    display_name: str
    digest: str | None = None
    family: str | None = None
    parameter_count: int | None = None
    parameter_label: str | None = None
    format: str | None = None
    quantization: str | None = None
    size_bytes: int | None = None
    context_length: int | None = None
    capabilities: list[str] = Field(default_factory=list)
    trainability: str | None = None
    metadata_source: str = "runtime"
    last_seen_at: datetime
    identity_strength: ModelIdentityStrength = ModelIdentityStrength.UNKNOWN


class RuntimeDescriptor(BaseModel):
    """Result of probing one candidate runtime endpoint."""

    model_config = ConfigDict(frozen=True)

    runtime_id: str
    kind: RuntimeKind
    endpoint: str
    source: RuntimeSource
    status: RuntimeStatus
    registered: bool
    server_running: bool
    installed: bool
    installed_not_running: bool
    identified_vendor: str | None = None
    evidence: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    models: list[ModelDescriptor] = Field(default_factory=list)
    last_seen_at: datetime


@dataclass(frozen=True)
class HttpResponse:
    """Bounded HTTP response used by runtime transports."""

    status: int
    text: str
    content_type: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)


class HttpTransport(Protocol):
    """Transport contract so adapters can be tested with injected clients."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout: float,
    ) -> HttpResponse: ...


class RuntimeAdapter(Protocol):
    """Interface implemented by every runtime discovery adapter."""

    runtime_id: str
    kind: RuntimeKind

    def probe(self) -> RuntimeDescriptor: ...


@dataclass(frozen=True)
class ProbeTarget:
    """One explicit runtime endpoint to probe."""

    runtime_id: str
    kind: RuntimeKind
    endpoint: str
    source: RuntimeSource
    adapter_type: AdapterType
    bearer_token: str | None = None
    timeout: float | None = None


def now_utc() -> datetime:
    return datetime.now(UTC)


def require_http_ok(response: HttpResponse, label: str) -> None:
    """Reject non-2xx responses without exposing response bodies."""
    if response.status < 200 or response.status >= 300:
        raise InvalidRuntimeResponseError(
            f"{label} returned HTTP {response.status}; runtime was not registered."
        )


def parse_json_object(response: HttpResponse, label: str) -> dict[str, Any]:
    """Parse a JSON object response or reject the runtime honestly."""
    import json

    try:
        payload = json.loads(response.text)
    except ValueError as error:
        raise InvalidRuntimeResponseError(
            f"{label} returned invalid JSON; runtime was not registered."
        ) from error
    if not isinstance(payload, dict):
        raise InvalidRuntimeResponseError(
            f"{label} returned a non-object payload; runtime was not registered."
        )
    return payload


def parse_json_list(response: HttpResponse, label: str) -> list[Any]:
    import json

    try:
        payload = json.loads(response.text)
    except ValueError as error:
        raise InvalidRuntimeResponseError(
            f"{label} returned invalid JSON; runtime was not registered."
        ) from error
    if not isinstance(payload, list):
        raise InvalidRuntimeResponseError(
            f"{label} returned a non-list payload; runtime was not registered."
        )
    return payload


def build_runtime_descriptor(
    *,
    runtime_id: str,
    kind: RuntimeKind,
    endpoint: str,
    source: RuntimeSource,
    installed: bool,
    server_running: bool,
    registered: bool,
    status: RuntimeStatus,
    evidence: Sequence[str],
    warnings: Sequence[str] = (),
    error: str | None = None,
    models: Sequence[ModelDescriptor] = (),
) -> RuntimeDescriptor:
    """Build a descriptor with the installed/server-off state normalized."""
    return RuntimeDescriptor(
        runtime_id=runtime_id,
        kind=kind,
        endpoint=endpoint,
        source=source,
        status=status,
        registered=registered,
        server_running=server_running,
        installed=installed,
        installed_not_running=installed and not server_running,
        identified_vendor=None,
        evidence=list(evidence),
        warnings=list(warnings),
        error=error,
        models=list(models),
        last_seen_at=now_utc(),
    )


class PullApproval(BaseModel):
    """Explicit user approval required before a native model pull plan exists."""

    model_config = ConfigDict(frozen=True)

    model_reference: str = Field(min_length=1)
    granted_by: Literal["user"] = "user"
    granted_at: datetime = Field(default_factory=now_utc)


def parse_parameter_label(label: str | None) -> int | None:
    """Convert labels like 4B or 1.5B into an integer parameter count."""
    if not label:
        return None
    normalized = label.strip().lower().replace("b", "")
    if not normalized:
        return None
    try:
        return int(float(normalized) * 1_000_000_000)
    except ValueError:
        return None
