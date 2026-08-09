"""Deterministic strategy composition tests."""

from __future__ import annotations

from tests.planning.helpers import (
    capability_facts,
    hardware_facts,
    model_facts,
    plan_inputs,
    policy,
    provider_facts,
)
from zana_core.planning.models import (
    StrategyComponent,
)
from zana_core.planning.strategy import compose_strategy


def compose(**inputs):
    policy_value = inputs.pop("policy", policy())
    capability = inputs.pop("capability", capability_facts())
    model = inputs.pop("model", model_facts())
    provider = inputs.pop("provider", provider_facts())
    hardware = inputs.pop("hardware", hardware_facts())
    inputs.pop("evaluation", None)
    return compose_strategy(
        policy=policy_value,
        capability=capability,
        model=model,
        provider=provider,
        hardware=hardware,
        **inputs,
    )


def test_rag_only_for_policy_capability():
    decision = compose(
        capability=capability_facts(
            has_knowledge=True,
            knowledge_citation_required=True,
            knowledge_bytes=4 * 1024 * 1024,
            has_tools=False,
            has_training=False,
            training_goal=None,
            train_record_count=None,
            validation_record_count=None,
            training_files_present=False,
        )
    )
    assert decision.components == (StrategyComponent.RAG,)
    assert any("RAG" in reason for reason in decision.reasons)
    assert decision.blockers == ()


def test_tools_only_when_trusted_builtin():
    decision = compose(
        capability=capability_facts(
            has_knowledge=False,
            has_tools=True,
            tool_ids=("zana.calculator",),
            has_training=False,
            training_goal=None,
            train_record_count=None,
            validation_record_count=None,
            training_files_present=False,
        )
    )
    assert decision.components == (StrategyComponent.TOOLS,)
    assert "zana.calculator" in decision.reasons[0]


def test_external_tools_warned_and_excluded():
    decision = compose(
        capability=capability_facts(
            has_knowledge=False,
            has_tools=True,
            tool_ids=("zana.calculator", "external.mcp.search"),
            has_training=False,
            training_goal=None,
            train_record_count=None,
            validation_record_count=None,
            training_files_present=False,
        )
    )
    assert decision.components == (StrategyComponent.TOOLS,)
    assert any("external MCP" in warning for warning in decision.warnings)


def test_adapter_eligible_full_composition():
    decision = compose()
    assert decision.components == (StrategyComponent.TOOLS, StrategyComponent.ADAPTER)
    assert decision.strategy_id == "tools+adapter"
    assert decision.blockers == ()
    assert any("task-oriented" in reason for reason in decision.reasons)


def test_rag_tools_adapter_full_composition():
    inputs = plan_inputs(
        capability=capability_facts(
            has_knowledge=True,
            knowledge_citation_required=True,
            knowledge_bytes=8 * 1024 * 1024,
        )
    )
    decision = compose(**inputs)
    assert decision.components == (
        StrategyComponent.RAG,
        StrategyComponent.TOOLS,
        StrategyComponent.ADAPTER,
    )


def test_book_documents_never_train():
    decision = compose(
        capability=capability_facts(
            has_knowledge=True,
            knowledge_bytes=100 * 1024 * 1024,
            has_training=False,
            training_goal=None,
            train_record_count=None,
            validation_record_count=None,
            training_files_present=False,
        )
    )
    assert StrategyComponent.ADAPTER not in decision.components
    assert any("no supervised training dataset" in reason for reason in decision.reasons)
    assert StrategyComponent.RAG in decision.components


def test_insufficient_examples_skip_adapter():
    decision = compose(capability=capability_facts(train_record_count=5, minimum_examples=100))
    assert StrategyComponent.ADAPTER not in decision.components


def test_non_eligible_training_goal_skips_adapter():
    decision = compose(capability=capability_facts(training_goal="creative_writing"))
    assert StrategyComponent.ADAPTER not in decision.components
    assert any("not an adapter-eligible" in reason for reason in decision.reasons)


def test_prefer_training_suppresses_scarce_example_rag_signal():
    decision = compose(
        policy=policy(prefer_training=True),
        capability=capability_facts(
            has_knowledge=True,
            knowledge_citation_required=False,
            knowledge_bytes=100_000,
            train_record_count=120,
            minimum_examples=100,
        ),
    )
    assert StrategyComponent.RAG not in decision.components
    assert StrategyComponent.ADAPTER in decision.components


def test_adapter_disabled_by_policy():
    decision = compose(
        policy=policy(allow_adapter_training=False),
    )
    assert StrategyComponent.ADAPTER not in decision.components
    assert any("disabled by policy" in reason for reason in decision.reasons)
