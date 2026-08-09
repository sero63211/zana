"""Strictness of immutable resource models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tests.resources.helpers import request, snapshot
from zana_core.resources.models import (
    OperationCategory,
    OperationRequest,
    PlatformLabel,
    ResourcePolicy,
    ResourceSnapshot,
)


def test_unknown_fields_forbidden():
    with pytest.raises(ValidationError):
        ResourceSnapshot(
            revision=0,
            platform=PlatformLabel.MACOS,
            memory_total_bytes=1,
            invented_field=True,
        )
    with pytest.raises(ValidationError):
        OperationRequest(id="x", category=OperationCategory.TINY, name="x", guess=1)
    with pytest.raises(ValidationError):
        ResourcePolicy(telemetry=True)


def test_negative_inputs_rejected():
    with pytest.raises(ValidationError):
        snapshot(memory_total=-1)
    with pytest.raises(ValidationError):
        request(memory=-1)
    with pytest.raises(ValidationError):
        request(items=-5)
    with pytest.raises(ValidationError):
        ResourcePolicy(memory_reserve_bytes=-10)
    with pytest.raises(ValidationError):
        ResourcePolicy(safety_reserve_fraction=1.0)
    with pytest.raises(ValidationError):
        ResourcePolicy(disk_overhead_fraction=-0.1)


def test_unbounded_values_rejected():
    too_big = 1 << 63
    with pytest.raises(ValidationError):
        request(memory=too_big)
    with pytest.raises(ValidationError):
        snapshot(memory_total=too_big)


def test_available_memory_cannot_exceed_total():
    with pytest.raises(ValidationError):
        snapshot(memory_total=8, memory_available=9)


def test_policy_merges_default_categories_and_rejects_mismatch():
    policy = ResourcePolicy()
    assert OperationCategory.TRAINING in policy.categories
    assert OperationCategory.TINY in policy.categories
    assert policy.category_limit(OperationCategory.TRAINING).max_concurrency == 1
    with pytest.raises(ValidationError):
        ResourcePolicy(
            categories={OperationCategory.BUILD: policy.category_limit(OperationCategory.TRAINING)}
        )


def test_models_are_frozen():
    snap = snapshot()
    with pytest.raises(ValidationError):
        snap.memory_total_bytes = 1
    req = request()
    with pytest.raises(ValidationError):
        req.name = "mutated"
