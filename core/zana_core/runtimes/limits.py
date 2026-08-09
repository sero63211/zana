"""Frozen strict limits for bounded runtime probe registries."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_TARGETS = 16
MAX_WORKERS = 4
MAX_TIMEOUT_SECONDS = 10.0
MAX_ENDPOINT_LENGTH = 2000
MAX_REFERENCE_LENGTH = 200
MAX_BEARER_TOKEN_BYTES = 4096
MAX_ENDPOINT_BYTES = 4096
MAX_REFERENCE_BYTES = 1024
MAX_EVIDENCE_ITEMS = 64
MAX_EVIDENCE_CHARS = 64_000
MAX_ERROR_CHARS = 512
MAX_MODELS = 128
MAX_MODEL_FIELD_BYTES = 256
MAX_MODEL_CAPABILITIES = 16
MAX_MODELS_TOTAL_BYTES = 262_144


class RuntimeProbeLimits(BaseModel):
    """Conservative hard maxima for one registry probe run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_targets: int = Field(default=MAX_TARGETS, strict=True, ge=1, le=MAX_TARGETS)
    max_workers: int = Field(default=MAX_WORKERS, strict=True, ge=1, le=MAX_WORKERS)
    max_timeout_seconds: float = Field(
        default=MAX_TIMEOUT_SECONDS,
        strict=True,
        gt=0,
        le=MAX_TIMEOUT_SECONDS,
        allow_inf_nan=False,
    )
    max_endpoint_length: int = Field(
        default=MAX_ENDPOINT_LENGTH, strict=True, ge=1, le=MAX_ENDPOINT_LENGTH
    )
    max_reference_length: int = Field(
        default=MAX_REFERENCE_LENGTH, strict=True, ge=1, le=MAX_REFERENCE_LENGTH
    )
    max_bearer_token_bytes: int = Field(
        default=MAX_BEARER_TOKEN_BYTES, strict=True, ge=1, le=MAX_BEARER_TOKEN_BYTES
    )
    max_endpoint_bytes: int = Field(
        default=MAX_ENDPOINT_BYTES, strict=True, ge=1, le=MAX_ENDPOINT_BYTES
    )
    max_reference_bytes: int = Field(
        default=MAX_REFERENCE_BYTES, strict=True, ge=1, le=MAX_REFERENCE_BYTES
    )
    max_evidence_items: int = Field(
        default=MAX_EVIDENCE_ITEMS, strict=True, ge=1, le=MAX_EVIDENCE_ITEMS
    )
    max_evidence_chars: int = Field(
        default=MAX_EVIDENCE_CHARS, strict=True, ge=1, le=MAX_EVIDENCE_CHARS
    )
    max_error_chars: int = Field(default=MAX_ERROR_CHARS, strict=True, ge=1, le=MAX_ERROR_CHARS)
    max_models: int = Field(default=MAX_MODELS, strict=True, ge=1, le=MAX_MODELS)
    max_model_field_bytes: int = Field(
        default=MAX_MODEL_FIELD_BYTES, strict=True, ge=1, le=MAX_MODEL_FIELD_BYTES
    )
    max_model_capabilities: int = Field(
        default=MAX_MODEL_CAPABILITIES, strict=True, ge=1, le=MAX_MODEL_CAPABILITIES
    )
    max_models_total_bytes: int = Field(
        default=MAX_MODELS_TOTAL_BYTES, strict=True, ge=1, le=MAX_MODELS_TOTAL_BYTES
    )

    @model_validator(mode="after")
    def _cross_validate(self) -> RuntimeProbeLimits:
        if self.max_workers > self.max_targets:
            raise ValueError("max_workers cannot exceed max_targets")
        return self


DEFAULT_PROBE_LIMITS = RuntimeProbeLimits()
