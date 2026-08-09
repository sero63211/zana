"""Deterministic context composition under an explicit token budget."""

from __future__ import annotations

from enum import Enum
from math import ceil

from pydantic import BaseModel, ConfigDict, Field


def estimate_tokens(text: str) -> int:
    """Deterministic approximation: one token per four characters.

    This is a stable protocol-level approximation, not a model tokenizer.
    Callers that know exact counts can set ``ContextItem.tokens``.
    """
    return max(1, ceil(len(text) / 4))


class ContextSectionKind(str, Enum):
    """Canonical context sections in spec order."""

    SYSTEM_POLICY = "system_policy"
    IMAGE_BEHAVIOR_POLICY = "image_behavior_policy"
    USER_INSTRUCTIONS = "user_instructions"
    MEMORY = "memory"
    EVIDENCE = "evidence"
    CONVERSATION = "conversation"
    TOOL_DEFINITIONS = "tool_definitions"


DEFAULT_SECTION_ORDER: tuple[ContextSectionKind, ...] = (
    ContextSectionKind.SYSTEM_POLICY,
    ContextSectionKind.IMAGE_BEHAVIOR_POLICY,
    ContextSectionKind.USER_INSTRUCTIONS,
    ContextSectionKind.MEMORY,
    ContextSectionKind.EVIDENCE,
    ContextSectionKind.CONVERSATION,
    ContextSectionKind.TOOL_DEFINITIONS,
)


class ContextItem(BaseModel):
    """One selectable unit inside a context section."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    text: str
    tokens: int | None = None

    def token_count(self) -> int:
        if self.tokens is not None:
            return self.tokens
        return estimate_tokens(self.text)


class ContextSection(BaseModel):
    """Ordered section content; protected sections are never truncated."""

    model_config = ConfigDict(extra="forbid")

    kind: ContextSectionKind
    items: list[ContextItem] = Field(default_factory=list)
    protected: bool = False

    def token_count(self) -> int:
        return sum(item.token_count() for item in self.items)


class TruncationDecision(BaseModel):
    """Record of every item dropped by the deterministic selector."""

    model_config = ConfigDict(extra="forbid")

    section: ContextSectionKind
    item_id: str
    reason: str = "token_budget_exceeded"
    tokens_saved: int


DEFAULT_PROTECTED_KINDS: frozenset[ContextSectionKind] = frozenset(
    {
        ContextSectionKind.SYSTEM_POLICY,
        ContextSectionKind.IMAGE_BEHAVIOR_POLICY,
        ContextSectionKind.USER_INSTRUCTIONS,
        ContextSectionKind.TOOL_DEFINITIONS,
    }
)


class ContextBudgetPolicy(BaseModel):
    """Budget plus explicit priorities for memory and evidence vs. old chat."""

    model_config = ConfigDict(extra="forbid")

    token_budget: int = Field(gt=0)
    evidence_priority: bool = True
    memory_priority: bool = True
    protected_kinds: frozenset[ContextSectionKind] = Field(
        default_factory=lambda: DEFAULT_PROTECTED_KINDS
    )

    @property
    def truncation_order(self) -> tuple[ContextSectionKind, ...]:
        """Oldest chat first; memory/evidence drop order honors priorities."""
        order: list[ContextSectionKind] = [ContextSectionKind.CONVERSATION]
        if self.memory_priority:
            order.append(ContextSectionKind.MEMORY)
        else:
            order.insert(0, ContextSectionKind.MEMORY)
        if self.evidence_priority:
            order.append(ContextSectionKind.EVIDENCE)
        else:
            order.insert(0, ContextSectionKind.EVIDENCE)
        return tuple(order)


class ComposeResult(BaseModel):
    """Selected context plus every truncation decision."""

    model_config = ConfigDict(extra="forbid")

    sections: list[ContextSection]
    total_tokens: int
    token_budget: int
    truncated: bool
    decisions: list[TruncationDecision] = Field(default_factory=list)


class ContextBudgetError(Exception):
    """Protected content cannot fit in the budget; nothing is silently dropped."""


def select_context(sections: list[ContextSection], policy: ContextBudgetPolicy) -> ComposeResult:
    """Select context deterministically under the token budget.

    Protected sections (system/permission constraints by default) are never
    truncated. If protected content alone cannot fit, ``ContextBudgetError``
    is raised instead of silently dropping a constraint.
    """
    kinds: dict[ContextSectionKind, ContextSection] = {}
    for section in sections:
        if section.kind in kinds:
            raise ValueError(f"duplicate context section {section.kind.value}")
        kinds[section.kind] = section

    ordered: list[ContextSection] = []
    for kind in DEFAULT_SECTION_ORDER:
        if kind in kinds:
            ordered.append(kinds[kind])

    selected = [section.model_copy(deep=True) for section in ordered]
    total = sum(section.token_count() for section in selected)
    decisions: list[TruncationDecision] = []

    if total <= policy.token_budget:
        return ComposeResult(
            sections=selected,
            total_tokens=total,
            token_budget=policy.token_budget,
            truncated=False,
            decisions=[],
        )

    by_kind = {section.kind: section for section in selected}
    for kind in policy.truncation_order:
        if total <= policy.token_budget:
            break
        section = by_kind.get(kind)
        if section is None or kind in policy.protected_kinds:
            continue
        kept: list[ContextItem] = []
        for item in section.items:
            if total > policy.token_budget:
                total -= item.token_count()
                decisions.append(
                    TruncationDecision(
                        section=kind,
                        item_id=item.id,
                        tokens_saved=item.token_count(),
                    )
                )
            else:
                kept.append(item)
        section.items = kept

    if total > policy.token_budget:
        protected_names = ", ".join(sorted(kind.value for kind in policy.protected_kinds))
        raise ContextBudgetError(
            "context exceeds budget even after truncating all allowed sections; "
            f"protected sections ({protected_names}) must fit, "
            f"overflow={total - policy.token_budget}"
        )

    return ComposeResult(
        sections=selected,
        total_tokens=total,
        token_budget=policy.token_budget,
        truncated=bool(decisions),
        decisions=decisions,
    )
