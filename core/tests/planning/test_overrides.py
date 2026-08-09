"""Explicit strategy overrides: incompatible choices block, never fall back."""

from __future__ import annotations

from tests.planning.helpers import plan_inputs, policy
from zana_core.planning.models import StrategyMode
from zana_core.planning.planner import BuildPlanner


def plan(**inputs):
    return BuildPlanner().plan(**plan_inputs(**inputs))


def test_explicit_rag_without_knowledge_blocks():
    result = plan(
        policy=policy(strategy=StrategyMode.RAG),
        capability=plan_inputs()["capability"].model_copy(update={"has_knowledge": False}),
    )
    assert any("STRATEGY_INCOMPATIBLE" in blocker for blocker in result.blockers)
    assert result.strategy.components == ()
    assert result.approvable is False


def test_explicit_tools_without_trusted_tool_blocks():
    result = plan(
        policy=policy(strategy=StrategyMode.TOOLS),
        capability=plan_inputs()["capability"].model_copy(
            update={"has_tools": False, "tool_ids": ()}
        ),
    )
    assert any("STRATEGY_INCOMPATIBLE" in blocker for blocker in result.blockers)


def test_explicit_adapter_with_missing_validation_blocks():
    result = plan(
        policy=policy(strategy=StrategyMode.ADAPTER),
        capability=plan_inputs()["capability"].model_copy(update={"validation_record_count": 0}),
    )
    assert any("STRATEGY_INCOMPATIBLE" in blocker for blocker in result.blockers)
    assert any("TRAINING_VALIDATION_MISSING" in blocker for blocker in result.blockers)


def test_explicit_rag_excludes_tools_and_adapter():
    result = plan(
        policy=policy(strategy=StrategyMode.RAG),
        capability=plan_inputs()["capability"].model_copy(
            update={
                "has_knowledge": True,
                "knowledge_citation_required": True,
                "knowledge_bytes": 4 * 1024 * 1024,
            }
        ),
    )
    assert result.strategy.components == ("rag",)


def test_explicit_combined_mode_keeps_exact_components():
    result = plan(
        policy=policy(strategy=StrategyMode.RAG_TOOLS_ADAPTER),
        capability=plan_inputs()["capability"].model_copy(
            update={
                "has_knowledge": True,
                "knowledge_citation_required": True,
                "knowledge_bytes": 4 * 1024 * 1024,
            }
        ),
    )
    assert result.strategy.strategy_id == "rag+tools+adapter"
