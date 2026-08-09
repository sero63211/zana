"""Frozen strict limits for bounded runtime probe registries."""

from __future__ import annotations

import math
from typing import Any

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


def _fresh_default_limits() -> RuntimeProbeLimits:
    """Return a fresh canonical default, never a shared mutable reference."""
    return RuntimeProbeLimits()


_LIMITS_SPEC: dict[str, dict[str, Any]] = {
    "max_targets": {"min": 1, "max": MAX_TARGETS, "float": False},
    "max_workers": {"min": 1, "max": MAX_WORKERS, "float": False},
    "max_timeout_seconds": {"min": 0.0, "max": MAX_TIMEOUT_SECONDS, "float": True},
    "max_endpoint_length": {"min": 1, "max": MAX_ENDPOINT_LENGTH, "float": False},
    "max_reference_length": {"min": 1, "max": MAX_REFERENCE_LENGTH, "float": False},
    "max_bearer_token_bytes": {"min": 1, "max": MAX_BEARER_TOKEN_BYTES, "float": False},
    "max_endpoint_bytes": {"min": 1, "max": MAX_ENDPOINT_BYTES, "float": False},
    "max_reference_bytes": {"min": 1, "max": MAX_REFERENCE_BYTES, "float": False},
    "max_evidence_items": {"min": 1, "max": MAX_EVIDENCE_ITEMS, "float": False},
    "max_evidence_chars": {"min": 1, "max": MAX_EVIDENCE_CHARS, "float": False},
    "max_error_chars": {"min": 1, "max": MAX_ERROR_CHARS, "float": False},
    "max_models": {"min": 1, "max": MAX_MODELS, "float": False},
    "max_model_field_bytes": {"min": 1, "max": MAX_MODEL_FIELD_BYTES, "float": False},
    "max_model_capabilities": {"min": 1, "max": MAX_MODEL_CAPABILITIES, "float": False},
    "max_models_total_bytes": {"min": 1, "max": MAX_MODELS_TOTAL_BYTES, "float": False},
}


def _validated_limits(limits: RuntimeProbeLimits) -> RuntimeProbeLimits:
    """Revalidate one limits instance field-by-field into a fresh instance.

    The caller may pass a frozen model that was mutated through
    ``object.__setattr__`` or built with ``model_construct``.  Those paths
    bypass Pydantic validation, so the registry reads the raw namespace once
    and rebuilds a fresh instance instead of trusting model_dump/model_copy
    hooks or a caller/global reference.
    """
    if type(limits) is not RuntimeProbeLimits:
        raise ValueError("limits must be a RuntimeProbeLimits instance")
    namespace = object.__getattribute__(limits, "__dict__")
    if type(namespace) is not dict:
        raise ValueError("limits is corrupted")
    validated: dict[str, Any] = {}
    for name, spec in _LIMITS_SPEC.items():
        if name not in namespace:
            raise ValueError("limits is missing a required field")
        value = namespace[name]
        if spec["float"] is True:
            if type(value) not in (int, float):
                raise ValueError("limits contains a non-numeric timeout")
            numeric = float(value)
            if math.isinf(numeric) or math.isnan(numeric):
                raise ValueError("limits contains a non-finite timeout")
            if numeric <= 0 or numeric > spec["max"]:
                raise ValueError("limits contains an out-of-range timeout")
            validated[name] = numeric
            continue
        if type(value) is not int:
            raise ValueError("limits contains a non-integer field")
        if value < 1 or value > spec["max"]:
            raise ValueError("limits contains an out-of-range field")
        validated[name] = value
    if validated["max_workers"] > validated["max_targets"]:
        raise ValueError("max_workers cannot exceed max_targets")
    return RuntimeProbeLimits(**validated)
