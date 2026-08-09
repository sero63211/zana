"""Confirmation-gated destructive reset for instance mutable state."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from zana_core.memory.models import MutableInstanceState


class ResetScope(str, Enum):
    """What a destructive reset clears."""

    CHAT = "chat"
    APPROVED_MEMORY = "approved_memory"
    FULL_MUTABLE_STATE = "full_mutable_state"


def _confirmation_token(instance_id: str, scope: ResetScope, state_revision: int) -> str:
    material = f"{instance_id}|{scope.value}|{state_revision}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class ResetPlan(BaseModel):
    """Auditable preconditions and expected cleared counts."""

    model_config = ConfigDict(extra="forbid")

    instance_id: str
    scope: ResetScope
    destructive: bool = True
    confirmation_token: str = Field(min_length=32)
    chat_turns: int
    approved_facts: int
    approved_preferences: int
    state_revision: int


class ResetAuditEntry(BaseModel):
    """What was actually cleared and when."""

    model_config = ConfigDict(extra="forbid")

    instance_id: str
    scope: ResetScope
    applied_at: datetime
    confirmation_fingerprint: str
    cleared_chat_turns: int
    cleared_facts: int
    cleared_preferences: int
    state_revision_after: int


class ResetResult(BaseModel):
    """A reset plan plus its auditable result and the new mutable state."""

    model_config = ConfigDict(extra="forbid")

    plan: ResetPlan
    audit: ResetAuditEntry
    state: MutableInstanceState


class ResetConfirmationError(Exception):
    """The confirmation token does not match the current state precondition."""


class ResetService:
    """Destructive reset service requiring a matching confirmation token."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))

    def create_plan(self, state: MutableInstanceState, scope: ResetScope) -> ResetPlan:
        """Plan a destructive reset and derive its required confirmation token."""
        return ResetPlan(
            instance_id=state.instance_id,
            scope=scope,
            destructive=True,
            confirmation_token=_confirmation_token(state.instance_id, scope, state.state_revision),
            chat_turns=len(state.conversation),
            approved_facts=len(state.approved_facts),
            approved_preferences=len(state.approved_preferences),
            state_revision=state.state_revision,
        )

    def apply(
        self,
        state: MutableInstanceState,
        scope: ResetScope,
        confirmation_token: str,
    ) -> ResetResult:
        """Apply the reset only when the confirmation token matches."""
        expected = _confirmation_token(state.instance_id, scope, state.state_revision)
        if not hmac.compare_digest(confirmation_token, expected):
            raise ResetConfirmationError(
                "confirmation token does not match the current state precondition"
            )

        plan = self.create_plan(state, scope)
        new_state = state.model_copy(deep=True)
        if scope in {ResetScope.CHAT, ResetScope.FULL_MUTABLE_STATE}:
            new_state.conversation = []
        if scope in {ResetScope.APPROVED_MEMORY, ResetScope.FULL_MUTABLE_STATE}:
            new_state.approved_facts = []
            new_state.approved_preferences = []
        new_state.state_revision += 1
        new_state.updated_at = self._clock()

        audit = ResetAuditEntry(
            instance_id=state.instance_id,
            scope=scope,
            applied_at=new_state.updated_at,
            confirmation_fingerprint=_token_fingerprint(confirmation_token),
            cleared_chat_turns=(
                len(state.conversation)
                if scope in {ResetScope.CHAT, ResetScope.FULL_MUTABLE_STATE}
                else 0
            ),
            cleared_facts=(
                len(state.approved_facts)
                if scope in {ResetScope.APPROVED_MEMORY, ResetScope.FULL_MUTABLE_STATE}
                else 0
            ),
            cleared_preferences=(
                len(state.approved_preferences)
                if scope in {ResetScope.APPROVED_MEMORY, ResetScope.FULL_MUTABLE_STATE}
                else 0
            ),
            state_revision_after=new_state.state_revision,
        )
        return ResetResult(plan=plan, audit=audit, state=new_state)
