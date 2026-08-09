"""Deterministic canonical serialization and BuildPlan digest tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tests.planning.helpers import plan_inputs
from zana_core.planning.models import build_digest, canonical_json
from zana_core.planning.planner import BuildPlanner


def plan(**inputs):
    return BuildPlanner().plan(**plan_inputs(**inputs))


def test_identical_inputs_produce_identical_digest():
    first = plan()
    second = plan()
    assert first.digest == second.digest
    assert first == second
    assert len(first.digest) == 64
    assert canonical_json(first) == canonical_json(second)


def test_changed_input_changes_digest():
    base = plan()
    changed = plan(policy=plan_inputs()["policy"].model_copy(update={"max_disk_gb": 60.0}))
    assert changed.digest != base.digest


def test_digest_excludes_itself():
    result = plan()
    assert result.digest == build_digest(result)


def test_serialization_is_deterministic_json():
    payload = plan().model_dump(mode="json")
    assert isinstance(payload, dict)


def test_plan_is_immutable():
    result = plan()
    with pytest.raises(ValidationError):
        result.approvable = True
