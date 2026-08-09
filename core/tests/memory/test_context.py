"""Tests for deterministic context selection under a token budget."""

import pytest

from zana_core.memory.context import (
    DEFAULT_SECTION_ORDER,
    ComposeResult,
    ContextBudgetError,
    ContextBudgetPolicy,
    ContextItem,
    ContextSection,
    ContextSectionKind,
    estimate_tokens,
    select_context,
)


def item(item_id: str, text: str) -> ContextItem:
    return ContextItem(id=item_id, text=text)


def section(kind: ContextSectionKind, texts: list[str], protected: bool = False) -> ContextSection:
    return ContextSection(
        kind=kind,
        items=[item(f"{kind.value}:{index}", text) for index, text in enumerate(texts)],
        protected=protected,
    )


def default_sections() -> list[ContextSection]:
    return [
        section(ContextSectionKind.SYSTEM_POLICY, ["no network", "no shell"]),
        section(ContextSectionKind.IMAGE_BEHAVIOR_POLICY, ["answer with evidence"]),
        section(ContextSectionKind.USER_INSTRUCTIONS, ["be concise"]),
        section(ContextSectionKind.MEMORY, ["user prefers concise"]),
        section(ContextSectionKind.EVIDENCE, ["chunk-one", "chunk-two"]),
        section(ContextSectionKind.CONVERSATION, ["old message", "recent message"]),
        section(ContextSectionKind.TOOL_DEFINITIONS, ["calculator"]),
    ]


def test_estimate_tokens_is_deterministic() -> None:
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2
    assert estimate_tokens("") == 1
    assert estimate_tokens("a" * 400) == 100


def test_all_content_fits_without_truncation() -> None:
    result = select_context(
        default_sections(),
        ContextBudgetPolicy(token_budget=10_000),
    )
    assert result.truncated is False
    assert result.decisions == []
    assert [section.kind for section in result.sections] == list(DEFAULT_SECTION_ORDER)
    assert result.total_tokens <= result.token_budget


def test_oldest_chat_truncated_first_with_decisions_recorded() -> None:
    sections = default_sections()
    result = select_context(
        sections,
        ContextBudgetPolicy(token_budget=31),
    )
    assert result.truncated is True
    conversation = next(
        section for section in result.sections if section.kind is ContextSectionKind.CONVERSATION
    )
    dropped_ids = {
        decision.item_id
        for decision in result.decisions
        if decision.section is ContextSectionKind.CONVERSATION
    }
    assert "conversation:0" in dropped_ids
    assert "conversation:1" in {item.id for item in conversation.items}
    assert result.total_tokens <= 31


def test_system_policy_never_dropped() -> None:
    sections = default_sections()
    result = select_context(sections, ContextBudgetPolicy(token_budget=20))
    system = next(
        section for section in result.sections if section.kind is ContextSectionKind.SYSTEM_POLICY
    )
    assert len(system.items) == 2
    assert all(item.text in {"no network", "no shell"} for item in system.items)


def test_protected_content_overflow_raises_instead_of_silent_drop() -> None:
    sections = default_sections()
    with pytest.raises(ContextBudgetError):
        select_context(sections, ContextBudgetPolicy(token_budget=1))


def test_user_instructions_protected_by_default() -> None:
    sections = default_sections()
    result = select_context(sections, ContextBudgetPolicy(token_budget=18))
    instructions = next(
        section
        for section in result.sections
        if section.kind is ContextSectionKind.USER_INSTRUCTIONS
    )
    assert len(instructions.items) == 1


def test_memory_priority_controls_drop_order() -> None:
    sections = default_sections()
    memory_first = select_context(
        sections,
        ContextBudgetPolicy(token_budget=18, memory_priority=False),
    )
    memory_section = next(
        section for section in memory_first.sections if section.kind is ContextSectionKind.MEMORY
    )
    assert memory_section.items == []


def test_evidence_priority_controls_drop_order() -> None:
    sections = default_sections()
    evidence_first = select_context(
        sections,
        ContextBudgetPolicy(token_budget=18, evidence_priority=False),
    )
    evidence = next(
        section
        for section in evidence_first.sections
        if section.kind is ContextSectionKind.EVIDENCE
    )
    assert evidence.items == []


def test_explicit_token_counts_override_estimate() -> None:
    sections = [
        ContextSection(
            kind=ContextSectionKind.CONVERSATION,
            items=[ContextItem(id="big", text="x", tokens=100)],
        )
    ]
    result = select_context(sections, ContextBudgetPolicy(token_budget=50))
    assert result.truncated is True
    assert result.total_tokens == 0
    assert result.decisions[0].tokens_saved == 100


def test_duplicate_section_raises() -> None:
    sections = [
        section(ContextSectionKind.CONVERSATION, ["a"]),
        section(ContextSectionKind.CONVERSATION, ["b"]),
    ]
    with pytest.raises(ValueError):
        select_context(sections, ContextBudgetPolicy(token_budget=100))


def test_compose_result_shape_is_typed() -> None:
    result = select_context(default_sections(), ContextBudgetPolicy(token_budget=1000))
    assert isinstance(result, ComposeResult)
    assert result.token_budget == 1000
    assert result.total_tokens >= 0
    assert isinstance(result.decisions, list)
