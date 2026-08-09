"""Strict frozen RuntimeProbeLimits tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from zana_core.runtimes.limits import (
    DEFAULT_PROBE_LIMITS,
    MAX_MODEL_FIELD_BYTES,
    MAX_MODELS_TOTAL_BYTES,
    RuntimeProbeLimits,
)


def test_default_limits_are_conservative():
    limits = DEFAULT_PROBE_LIMITS
    assert limits.max_targets == 16
    assert limits.max_workers == 4
    assert 0 < limits.max_timeout_seconds <= 10
    assert limits.max_endpoint_length == 2000


def test_limits_are_frozen_and_forbid_extra():
    with pytest.raises(ValidationError):
        DEFAULT_PROBE_LIMITS.max_targets = 32
    with pytest.raises(ValidationError):
        RuntimeProbeLimits(invented_field=True)


def test_absurd_values_rejected():
    with pytest.raises(ValidationError):
        RuntimeProbeLimits(max_targets=1000)
    with pytest.raises(ValidationError):
        RuntimeProbeLimits(max_workers=100)
    with pytest.raises(ValidationError):
        RuntimeProbeLimits(max_timeout_seconds=300)
    with pytest.raises(ValidationError):
        RuntimeProbeLimits(max_timeout_seconds=0)
    with pytest.raises(ValidationError):
        RuntimeProbeLimits(max_evidence_items=0)


def test_cross_field_worker_target_relation():
    with pytest.raises(ValidationError):
        RuntimeProbeLimits(max_targets=2, max_workers=4)
    valid = RuntimeProbeLimits(max_targets=8, max_workers=2)
    assert valid.max_workers <= valid.max_targets


def test_timeout_must_be_finite():
    with pytest.raises(ValidationError):
        RuntimeProbeLimits(max_timeout_seconds=float("nan"))
    with pytest.raises(ValidationError):
        RuntimeProbeLimits(max_timeout_seconds=float("inf"))
    with pytest.raises(ValidationError):
        RuntimeProbeLimits(max_timeout_seconds=float("-inf"))


def test_hard_output_and_byte_bounds_exist():
    limits = DEFAULT_PROBE_LIMITS
    assert limits.max_bearer_token_bytes > 0
    assert limits.max_endpoint_bytes > 0
    assert limits.max_reference_bytes > 0
    assert 0 < limits.max_models <= 128
    assert limits.max_model_field_bytes == MAX_MODEL_FIELD_BYTES
    assert limits.max_model_capabilities > 0
    assert limits.max_models_total_bytes == MAX_MODELS_TOTAL_BYTES


def test_absurd_output_and_byte_values_rejected():
    with pytest.raises(ValidationError):
        RuntimeProbeLimits(max_models=0)
    with pytest.raises(ValidationError):
        RuntimeProbeLimits(max_model_field_bytes=0)
    with pytest.raises(ValidationError):
        RuntimeProbeLimits(max_model_capabilities=0)
    with pytest.raises(ValidationError):
        RuntimeProbeLimits(max_bearer_token_bytes=0)


def test_bool_and_wrong_numeric_types_rejected_for_every_public_field():
    base = DEFAULT_PROBE_LIMITS
    int_fields = [
        "max_targets",
        "max_workers",
        "max_endpoint_length",
        "max_reference_length",
        "max_bearer_token_bytes",
        "max_endpoint_bytes",
        "max_reference_bytes",
        "max_evidence_items",
        "max_evidence_chars",
        "max_error_chars",
        "max_models",
        "max_model_field_bytes",
        "max_model_capabilities",
        "max_models_total_bytes",
    ]
    for name in int_fields:
        current = getattr(base, name)
        for bad in (True, False, str(current)):
            with pytest.raises(ValidationError):
                RuntimeProbeLimits(**{name: bad})
        with pytest.raises(ValidationError):
            RuntimeProbeLimits(**{name: float(current)})
    for bad in (True, False, "1.5"):
        with pytest.raises(ValidationError):
            RuntimeProbeLimits(max_timeout_seconds=bad)


def test_combinations_cannot_bypass_cross_validation_with_coercion():
    with pytest.raises(ValidationError):
        RuntimeProbeLimits(max_targets=2, max_workers="4")
    with pytest.raises(ValidationError):
        RuntimeProbeLimits(max_targets=2, max_workers=True)
    with pytest.raises(ValidationError):
        RuntimeProbeLimits(max_workers=4.0, max_targets=4)
