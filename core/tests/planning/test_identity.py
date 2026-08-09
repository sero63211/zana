"""Exact model identity and compatibility rules."""

from __future__ import annotations

from tests.planning.helpers import model_facts, plan_inputs
from zana_core.domain.enums import ModelIdentityStrength
from zana_core.planning.models import (
    StrategyComponent,
    StrategyMode,
)
from zana_core.planning.planner import BuildPlanner


def plan(**inputs):
    return BuildPlanner().plan(**plan_inputs(**inputs))


def test_display_name_only_identity_never_allows_adapter():
    result = plan(
        model=model_facts(
            identity_strength=ModelIdentityStrength.DISPLAY_NAME_ONLY,
            digest=None,
            runtime_identity=None,
            training_source_identity=None,
            adapter_base_identity=None,
        ),
        policy=plan_inputs()["policy"].model_copy(update={"strategy": StrategyMode.ADAPTER}),
    )
    assert StrategyComponent.ADAPTER not in result.strategy.components
    assert any("MODEL_IDENTITY_WEAK" in blocker for blocker in result.blockers)


def test_unknown_identity_never_allows_adapter():
    result = plan(
        model=model_facts(
            identity_strength=ModelIdentityStrength.UNKNOWN,
            digest=None,
            training_source_identity=None,
            adapter_base_identity=None,
        )
    )
    assert StrategyComponent.ADAPTER not in result.strategy.components
    assert any("not proven" in reason for reason in result.strategy.reasons)


def test_mismatched_training_and_adapter_base_blocks_adapter():
    result = plan(
        model=model_facts(
            training_source_identity="b" * 64,
            adapter_base_identity="c" * 64,
        )
    )
    assert StrategyComponent.ADAPTER not in result.strategy.components


def test_digest_mismatch_blocks_adapter():
    result = plan(model=model_facts(digest="d" * 64))
    assert StrategyComponent.ADAPTER not in result.strategy.components


def test_runtime_identity_conflict_blocks_adapter():
    result = plan(model=model_facts(runtime_identity="e" * 64))
    assert StrategyComponent.ADAPTER not in result.strategy.components


def test_auto_mode_weak_identity_is_not_a_plan_blocker():
    result = plan(
        model=model_facts(
            identity_strength=ModelIdentityStrength.RUNTIME_MODEL_ID,
            digest=None,
            runtime_identity="runtime-id",
            training_source_identity=None,
            adapter_base_identity=None,
        ),
        capability=plan_inputs()["capability"].model_copy(update={"training_optional": True}),
    )
    assert StrategyComponent.ADAPTER not in result.strategy.components
    assert result.strategy.blockers == ()


def test_explicit_adapter_override_never_silently_falls_back():
    result = plan(
        model=model_facts(
            identity_strength=ModelIdentityStrength.DISPLAY_NAME_ONLY,
            digest=None,
            training_source_identity=None,
            adapter_base_identity=None,
        ),
        policy=plan_inputs()["policy"].model_copy(update={"strategy": StrategyMode.ADAPTER}),
    )
    assert result.strategy.components == ()
    assert any("STRATEGY_INCOMPATIBLE" in blocker for blocker in result.blockers)
    assert result.approvable is False


def test_runtime_offline_blocks_build():
    result = plan(model=model_facts(runtime_online=False))
    assert any("MODEL_RUNTIME_UNAVAILABLE" in blocker for blocker in result.blockers)


def test_context_length_below_capability_minimum_blocks_build():
    result = plan(model=model_facts(context_length=2048))
    assert any("MODEL_CONTEXT_INSUFFICIENT" in blocker for blocker in result.blockers)


def test_missing_required_model_capability_blocks_build():
    result = plan(model=model_facts(capabilities=("vision",)))
    assert any("MODEL_CAPABILITY_MISSING" in blocker for blocker in result.blockers)


def test_verify_requires_evaluation_suites():
    result = plan(
        evaluation=plan_inputs()["evaluation"].model_copy(
            update={"has_domain": False, "has_regression": False, "domain_records": None}
        )
    )
    assert any("VERIFICATION_REQUIRES_EVALUATION" in blocker for blocker in result.blockers)


def test_leakage_signal_blocks_explicit_adapter():
    result = plan(
        capability=plan_inputs()["capability"].model_copy(update={"leakage_ok": False}),
        policy=plan_inputs()["policy"].model_copy(update={"strategy": StrategyMode.ADAPTER}),
    )
    assert StrategyComponent.ADAPTER not in result.strategy.components
    assert any("TRAINING_LEAKAGE" in blocker for blocker in result.blockers)
