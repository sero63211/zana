"""Tests for the memory approval workflow and auto-memory category policy."""

from datetime import UTC, datetime

import pytest

from zana_core.domain.enums import MemoryStatus
from zana_core.memory.approval import (
    AutoMemoryNotEnabledError,
    MemoryApprovalService,
    MemoryAutoPolicy,
    MemoryServiceError,
    ProposalAlreadyResolvedError,
    ProposalNotFoundError,
)
from zana_core.memory.models import (
    ApprovalDecision,
    ApprovalSource,
    MemoryCategory,
    MemoryProposal,
    MemoryType,
)

FIXED = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def make_service() -> MemoryApprovalService:
    return MemoryApprovalService(clock=lambda: FIXED)


def test_propose_registers_pending_only() -> None:
    service = make_service()
    proposal = service.propose(
        "inst-1",
        memory_type=MemoryType.PREFERENCE,
        category=MemoryCategory.PREFERENCE,
        content="User prefers concise answers.",
        source_message_id="msg-1",
    )
    assert proposal.status is MemoryStatus.PENDING
    assert proposal.provenance is None
    assert service.active_memories("inst-1") == []
    assert [item.id for item in service.proposals("inst-1")] == [proposal.id]


def test_user_approve_creates_active_record_with_provenance() -> None:
    service = make_service()
    proposal = service.propose(
        "inst-1",
        memory_type=MemoryType.PREFERENCE,
        category=MemoryCategory.PREFERENCE,
        content="User prefers concise answers.",
        source_message_id="msg-1",
    )
    record = service.approve(
        proposal.id,
        source=ApprovalSource.USER_EXPLICIT,
        reason="user confirmed",
    )
    assert record.status is MemoryStatus.APPROVED
    assert record.category is MemoryCategory.PREFERENCE
    assert record.provenance.decision is ApprovalDecision.APPROVED
    assert record.provenance.source is ApprovalSource.USER_EXPLICIT
    assert record.provenance.reason == "user confirmed"
    assert record.provenance.source_message_id == "msg-1"
    assert service.active_memories("inst-1") == [record]
    assert service.proposals("inst-1") == []


def test_rejected_proposal_never_becomes_active_memory() -> None:
    service = make_service()
    proposal = service.propose(
        "inst-1",
        memory_type=MemoryType.FACT,
        category=MemoryCategory.FACT,
        content="rejected fact",
    )
    rejected = service.reject(proposal.id, reason="not useful")
    assert rejected.status is MemoryStatus.REJECTED
    assert rejected.provenance is not None
    assert rejected.provenance.source is ApprovalSource.USER_EXPLICIT
    assert service.active_memories("inst-1") == []
    assert [item.id for item in service.proposals("inst-1")] == [proposal.id]


def test_auto_approve_allowed_category() -> None:
    service = make_service()
    proposal = service.propose(
        "inst-1",
        memory_type=MemoryType.PREFERENCE,
        category=MemoryCategory.PREFERENCE,
        content="shorter answers please",
    )
    policy = MemoryAutoPolicy(auto_approve_categories=frozenset({MemoryCategory.PREFERENCE}))
    record = service.auto_approve(proposal.id, policy=policy)
    assert record.provenance.source is ApprovalSource.AUTO_MEMORY_POLICY
    assert service.active_memories("inst-1") == [record]


def test_auto_approve_disallowed_category_raises_and_stays_pending() -> None:
    service = make_service()
    proposal = service.propose(
        "inst-1",
        memory_type=MemoryType.FACT,
        category=MemoryCategory.FACT,
        content="unrequested fact",
    )
    policy = MemoryAutoPolicy(auto_approve_categories=frozenset({MemoryCategory.PREFERENCE}))
    with pytest.raises(AutoMemoryNotEnabledError):
        service.auto_approve(proposal.id, policy=policy)
    assert service.proposals("inst-1")[0].status is MemoryStatus.PENDING
    assert service.active_memories("inst-1") == []


def test_auto_source_requires_policy() -> None:
    service = make_service()
    proposal = service.propose(
        "inst-1",
        memory_type=MemoryType.PREFERENCE,
        category=MemoryCategory.PREFERENCE,
        content="anything",
    )
    with pytest.raises(AutoMemoryNotEnabledError):
        service.approve(proposal.id, source=ApprovalSource.AUTO_MEMORY_POLICY)


def test_double_decision_raises() -> None:
    service = make_service()
    proposal = service.propose(
        "inst-1",
        memory_type=MemoryType.FACT,
        category=MemoryCategory.FACT,
        content="one-time fact",
    )
    service.approve(proposal.id, source=ApprovalSource.USER_EXPLICIT)
    with pytest.raises(ProposalAlreadyResolvedError):
        service.approve(proposal.id, source=ApprovalSource.USER_EXPLICIT)
    with pytest.raises(ProposalAlreadyResolvedError):
        service.reject(proposal.id)


def test_reject_then_approve_raises() -> None:
    service = make_service()
    proposal = service.propose(
        "inst-1",
        memory_type=MemoryType.FACT,
        category=MemoryCategory.FACT,
        content="fact",
    )
    service.reject(proposal.id)
    with pytest.raises(ProposalAlreadyResolvedError):
        service.approve(proposal.id, source=ApprovalSource.USER_EXPLICIT)


def test_unknown_proposal_raises() -> None:
    service = make_service()
    with pytest.raises(ProposalNotFoundError):
        service.approve("missing", source=ApprovalSource.USER_EXPLICIT)
    with pytest.raises(ProposalNotFoundError):
        service.reject("missing")


def test_submit_forces_pending_and_duplicate_id_raises() -> None:
    service = make_service()
    incoming = MemoryProposal(
        id="p-1",
        instance_id="inst-1",
        type=MemoryType.FACT,
        category=MemoryCategory.FACT,
        content="fact",
        proposed_at=FIXED,
        status=MemoryStatus.APPROVED,
    )
    submitted = service.submit(incoming)
    assert submitted.status is MemoryStatus.PENDING
    with pytest.raises(MemoryServiceError):
        service.submit(incoming)


def test_active_memories_filter_by_instance() -> None:
    service = make_service()
    first = service.propose(
        "inst-1",
        memory_type=MemoryType.FACT,
        category=MemoryCategory.FACT,
        content="a",
    )
    second = service.propose(
        "inst-2",
        memory_type=MemoryType.PREFERENCE,
        category=MemoryCategory.PREFERENCE,
        content="b",
    )
    service.approve(first.id, source=ApprovalSource.USER_EXPLICIT)
    service.approve(second.id, source=ApprovalSource.USER_EXPLICIT)
    assert [item.instance_id for item in service.active_memories("inst-1")] == ["inst-1"]
    assert len(service.active_memories()) == 2
