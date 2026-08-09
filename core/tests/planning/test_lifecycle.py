"""Lifecycle phase ordering and cancellation-checkpoint metadata tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tests.planning.helpers import plan_inputs
from zana_core.planning.planner import BuildPlanner


def plan(**inputs):
    return BuildPlanner().plan(**plan_inputs(**inputs))


def test_canonical_phase_order_for_full_strategy():
    result = plan()
    names = result.lifecycle.phase_names
    assert names == (
        "ANALYZING",
        "BASELINE_RUNNING",
        "PLANNED",
        "TRAINING_ADAPTER",
        "MATERIALIZING",
        "EVALUATING",
        "PACKING",
        "VERIFIED",
    )


def test_acquisition_phase_present_when_checkpoint_download_needed():
    result = plan(
        model=plan_inputs()["model"].model_copy(update={"training_source_available": False})
    )
    names = result.lifecycle.phase_names
    assert "ACQUIRING_APPROVED_ARTIFACTS" in names
    acquisition = next(
        item
        for item in result.lifecycle.phases
        if item.phase.value == "ACQUIRING_APPROVED_ARTIFACTS"
    )
    assert acquisition.required is True
    assert acquisition.checkpoint.subprocess_termination_required is True


def test_rag_phase_only_when_knowledge_selected():
    result = plan(
        capability=plan_inputs()["capability"].model_copy(
            update={
                "has_knowledge": True,
                "knowledge_citation_required": True,
                "knowledge_bytes": 4 * 1024 * 1024,
            }
        )
    )
    assert "BUILDING_KNOWLEDGE" in result.lifecycle.phase_names


def test_no_rag_or_adapter_phases_for_tools_only():
    result = plan(
        capability=plan_inputs()["capability"].model_copy(
            update={
                "has_tools": True,
                "tool_ids": ("zana.calculator",),
                "has_training": False,
                "training_goal": None,
                "train_record_count": None,
                "validation_record_count": None,
                "training_files_present": False,
            }
        )
    )
    names = result.lifecycle.phase_names
    assert "BUILDING_KNOWLEDGE" not in names
    assert "TRAINING_ADAPTER" not in names
    assert "MATERIALIZING" not in names


def test_verified_phase_omitted_when_verification_not_required():
    result = plan(policy=plan_inputs()["policy"].model_copy(update={"require_verification": False}))
    assert "VERIFIED" not in result.lifecycle.phase_names


def test_terminal_phase_is_immutable_checkpoint():
    result = plan()
    verified = result.lifecycle.phases[-1]
    assert verified.phase.value == "VERIFIED"
    assert verified.checkpoint.safe_cancellation_supported is False
    assert verified.checkpoint.transaction_rollback_required is False


def test_phases_are_frozen_and_ordered():
    result = plan()
    with pytest.raises(ValidationError):
        result.lifecycle.phases = ()
    order = {
        "ANALYZING": 0,
        "BASELINE_RUNNING": 1,
        "PLANNED": 2,
        "ACQUIRING_APPROVED_ARTIFACTS": 3,
        "BUILDING_KNOWLEDGE": 4,
        "TRAINING_ADAPTER": 5,
        "MATERIALIZING": 6,
        "EVALUATING": 7,
        "PACKING": 8,
        "VERIFIED": 9,
    }
    names = result.lifecycle.phase_names
    assert [order[name] for name in names] == sorted(order[name] for name in names)
