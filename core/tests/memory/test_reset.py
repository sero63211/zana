"""Tests for confirmation-gated destructive resets."""

import hashlib
from datetime import UTC, datetime

import pytest

from zana_core.domain.enums import MemoryStatus, MessageRole
from zana_core.memory.models import (
    ApprovalDecision,
    ApprovalProvenance,
    ApprovalSource,
    ConversationTurn,
    MemoryCategory,
    MemoryRecord,
    MemoryType,
    MutableInstanceState,
)
from zana_core.memory.reset import (
    ResetConfirmationError,
    ResetScope,
    ResetService,
)

FIXED = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def turn(turn_id: str, text: str) -> ConversationTurn:
    return ConversationTurn(
        id=turn_id,
        role=MessageRole.USER,
        content=text,
        created_at=FIXED,
    )


def memory(record_id: str) -> MemoryRecord:
    return MemoryRecord(
        id=record_id,
        instance_id="inst-1",
        type=MemoryType.FACT,
        category=MemoryCategory.FACT,
        content=f"fact {record_id}",
        created_at=FIXED,
        status=MemoryStatus.APPROVED,
        provenance=ApprovalProvenance(
            decision=ApprovalDecision.APPROVED,
            source=ApprovalSource.USER_EXPLICIT,
            decided_at=FIXED,
        ),
    )


def state(revision: int = 3) -> MutableInstanceState:
    return MutableInstanceState(
        instance_id="inst-1",
        state_revision=revision,
        conversation=[turn("t1", "hello"), turn("t2", "bye")],
        approved_facts=[memory("m1")],
        approved_preferences=[],
        updated_at=FIXED,
    )


def make_service() -> ResetService:
    return ResetService(clock=lambda: FIXED)


def test_chat_reset_clears_only_chat() -> None:
    service = make_service()
    plan = service.create_plan(state(), ResetScope.CHAT)
    result = service.apply(state(), ResetScope.CHAT, plan.confirmation_token)
    assert result.plan.destructive is True
    assert result.audit.cleared_chat_turns == 2
    assert result.audit.cleared_facts == 0
    assert result.state.conversation == []
    assert len(result.state.approved_facts) == 1
    assert result.state.state_revision == 4


def test_approved_memory_reset_clears_only_memory() -> None:
    service = make_service()
    plan = service.create_plan(state(), ResetScope.APPROVED_MEMORY)
    result = service.apply(state(), ResetScope.APPROVED_MEMORY, plan.confirmation_token)
    assert result.audit.cleared_facts == 1
    assert result.audit.cleared_preferences == 0
    assert result.audit.cleared_chat_turns == 0
    assert result.state.approved_facts == []
    assert len(result.state.conversation) == 2


def test_full_reset_clears_chat_and_memory() -> None:
    service = make_service()
    plan = service.create_plan(state(), ResetScope.FULL_MUTABLE_STATE)
    result = service.apply(state(), ResetScope.FULL_MUTABLE_STATE, plan.confirmation_token)
    assert result.state.conversation == []
    assert result.state.approved_facts == []
    assert result.state.approved_preferences == []
    assert result.state.state_revision == 4


def test_wrong_confirmation_token_raises_without_mutation() -> None:
    service = make_service()
    current = state()
    with pytest.raises(ResetConfirmationError):
        service.apply(current, ResetScope.CHAT, "not-the-token")
    assert len(current.conversation) == 2
    assert current.state_revision == 3


def test_stale_token_from_older_revision_is_rejected() -> None:
    service = make_service()
    old = state(revision=3)
    stale_token = service.create_plan(old, ResetScope.CHAT).confirmation_token
    newer = state(revision=4)
    with pytest.raises(ResetConfirmationError):
        service.apply(newer, ResetScope.CHAT, stale_token)


def test_confirmation_token_is_scope_specific() -> None:
    service = make_service()
    chat_token = service.create_plan(state(), ResetScope.CHAT).confirmation_token
    with pytest.raises(ResetConfirmationError):
        service.apply(state(), ResetScope.APPROVED_MEMORY, chat_token)


def test_audit_records_fingerprint_and_revision() -> None:
    service = make_service()
    plan = service.create_plan(state(), ResetScope.FULL_MUTABLE_STATE)
    result = service.apply(state(), ResetScope.FULL_MUTABLE_STATE, plan.confirmation_token)
    expected = hashlib.sha256(plan.confirmation_token.encode("utf-8")).hexdigest()
    assert result.audit.confirmation_fingerprint == expected
    assert result.audit.confirmation_fingerprint != plan.confirmation_token
    assert result.audit.state_revision_after == 4
    assert result.audit.applied_at == FIXED


def test_plan_reports_expected_counts() -> None:
    service = make_service()
    plan = service.create_plan(state(), ResetScope.FULL_MUTABLE_STATE)
    assert plan.chat_turns == 2
    assert plan.approved_facts == 1
    assert plan.approved_preferences == 0
    assert plan.state_revision == 3
    assert len(plan.confirmation_token) == 64
