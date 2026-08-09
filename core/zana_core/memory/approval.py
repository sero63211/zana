"""Memory approval workflow and narrow auto-memory category policy."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from zana_core.domain.enums import MemoryStatus
from zana_core.memory.models import (
    ApprovalDecision,
    ApprovalProvenance,
    ApprovalSource,
    MemoryCategory,
    MemoryProposal,
    MemoryRecord,
    MemoryType,
)


class MemoryAutoPolicy(BaseModel):
    """User-enabled automatic approval, scoped to explicit categories only."""

    model_config = ConfigDict(extra="forbid")

    auto_approve_categories: frozenset[MemoryCategory] = Field(default_factory=frozenset)

    def allows(self, category: MemoryCategory) -> bool:
        return category in self.auto_approve_categories


class MemoryServiceError(Exception):
    """Base error for the memory approval service."""


class ProposalNotFoundError(MemoryServiceError):
    """The referenced proposal does not exist."""


class ProposalAlreadyResolvedError(MemoryServiceError):
    """The proposal is no longer pending and cannot be decided again."""


class AutoMemoryNotEnabledError(MemoryServiceError):
    """Auto-approval is not enabled for the proposal category."""


class MemoryApprovalService:
    """Deterministic in-memory approval workflow.

    A proposal only becomes active memory through :meth:`approve` (or
    :meth:`auto_approve`, which is identical but requires the category to be
    enabled in an explicit user policy). Rejected and pending proposals are
    never returned as active memory.
    """

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._proposals: dict[str, MemoryProposal] = {}
        self._records: dict[str, MemoryRecord] = {}

    def propose(
        self,
        instance_id: str,
        *,
        memory_type: MemoryType,
        category: MemoryCategory,
        content: str,
        source_message_id: str | None = None,
        proposal_id: str | None = None,
    ) -> MemoryProposal:
        """Create and register a pending proposal."""
        proposal = MemoryProposal(
            id=proposal_id or f"proposal-{len(self._proposals) + 1}",
            instance_id=instance_id,
            type=memory_type,
            category=category,
            content=content,
            source_message_id=source_message_id,
            proposed_at=self._clock(),
        )
        return self.submit(proposal)

    def submit(self, proposal: MemoryProposal) -> MemoryProposal:
        """Register a proposal; any incoming status is forced to PENDING."""
        if proposal.id in self._proposals or proposal.id in self._records:
            raise MemoryServiceError(f"proposal id already exists: {proposal.id}")
        pending = proposal.model_copy(update={"status": MemoryStatus.PENDING, "provenance": None})
        self._proposals[proposal.id] = pending
        return pending

    def approve(
        self,
        proposal_id: str,
        *,
        source: ApprovalSource,
        reason: str = "",
        policy: MemoryAutoPolicy | None = None,
    ) -> MemoryRecord:
        """Approve a pending proposal and return the active memory record."""
        proposal = self._get_pending(proposal_id)
        if source is ApprovalSource.AUTO_MEMORY_POLICY and (
            policy is None or not policy.allows(proposal.category)
        ):
            raise AutoMemoryNotEnabledError(
                f"auto-memory is not enabled for category {proposal.category.value}"
            )
        provenance = ApprovalProvenance(
            decision=ApprovalDecision.APPROVED,
            source=source,
            decided_at=self._clock(),
            reason=reason,
            source_message_id=proposal.source_message_id,
        )
        record = MemoryRecord(
            id=proposal.id,
            instance_id=proposal.instance_id,
            type=proposal.type,
            category=proposal.category,
            content=proposal.content,
            source_message_id=proposal.source_message_id,
            created_at=proposal.proposed_at,
            status=MemoryStatus.APPROVED,
            provenance=provenance,
        )
        del self._proposals[proposal_id]
        self._records[record.id] = record
        return record

    def auto_approve(
        self,
        proposal_id: str,
        *,
        policy: MemoryAutoPolicy,
        reason: str = "",
    ) -> MemoryRecord:
        """Approve only when the explicit auto-memory policy allows the category."""
        return self.approve(
            proposal_id,
            source=ApprovalSource.AUTO_MEMORY_POLICY,
            reason=reason,
            policy=policy,
        )

    def reject(self, proposal_id: str, *, reason: str = "") -> MemoryProposal:
        """Reject a pending proposal; rejection is always user explicit."""
        proposal = self._get_pending(proposal_id)
        provenance = ApprovalProvenance(
            decision=ApprovalDecision.REJECTED,
            source=ApprovalSource.USER_EXPLICIT,
            decided_at=self._clock(),
            reason=reason,
            source_message_id=proposal.source_message_id,
        )
        rejected = proposal.model_copy(
            update={"status": MemoryStatus.REJECTED, "provenance": provenance}
        )
        self._proposals[proposal_id] = rejected
        return rejected

    def active_memories(self, instance_id: str | None = None) -> list[MemoryRecord]:
        """Return approved records in deterministic creation order."""
        records = sorted(self._records.values(), key=lambda record: (record.created_at, record.id))
        if instance_id is not None:
            records = [record for record in records if record.instance_id == instance_id]
        return records

    def proposals(self, instance_id: str | None = None) -> list[MemoryProposal]:
        """Return proposals (pending or rejected) in deterministic order."""
        proposals = sorted(
            self._proposals.values(), key=lambda proposal: (proposal.proposed_at, proposal.id)
        )
        if instance_id is not None:
            proposals = [proposal for proposal in proposals if proposal.instance_id == instance_id]
        return proposals

    def _get_pending(self, proposal_id: str) -> MemoryProposal:
        if proposal_id in self._records:
            raise ProposalAlreadyResolvedError(f"proposal {proposal_id} is already resolved")
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise ProposalNotFoundError(f"no proposal with id {proposal_id}")
        if proposal.status is not MemoryStatus.PENDING:
            raise ProposalAlreadyResolvedError(
                f"proposal {proposal_id} is already {proposal.status.value}"
            )
        return proposal
