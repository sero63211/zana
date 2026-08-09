"""Tests for typed memory and instance pointer models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from zana_core.domain.enums import MemoryStatus, MessageRole
from zana_core.memory.models import (
    ConversationTurn,
    ImagePointer,
    MemoryCategory,
    MemoryProposal,
    MemoryType,
)

FIXED = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def test_image_pointer_is_frozen() -> None:
    pointer = ImagePointer(digest="sha256:image-v1")
    with pytest.raises(ValidationError):
        pointer.digest = "sha256:image-v2"


def test_proposal_defaults_to_pending_without_provenance() -> None:
    proposal = MemoryProposal(
        id="p-1",
        instance_id="inst-1",
        type=MemoryType.FACT,
        category=MemoryCategory.FACT,
        content="fact",
        proposed_at=FIXED,
    )
    assert proposal.status is MemoryStatus.PENDING
    assert proposal.provenance is None
    assert proposal.category is not None


def test_conversation_turn_typed_roles() -> None:
    turn = ConversationTurn(
        id="t-1",
        role=MessageRole.ASSISTANT,
        content="answer",
        created_at=FIXED,
    )
    assert turn.role is MessageRole.ASSISTANT
    assert turn.metadata == {}
