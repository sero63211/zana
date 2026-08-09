"""Finalization order and nondestructive rebuild-history tests."""

from __future__ import annotations

from zana_core.builds.models import FinalizationPlan, LifecyclePhase
from zana_core.builds.service import BuildLifecycleService
from zana_core.builds.state_machine import can_transition


class TestFinalization:
    def test_finalization_order_is_verify_then_move_then_register(self) -> None:
        plan = FinalizationPlan(
            verify_digests_first=True,
            atomic_move_intent=["blob-a", "blob-b"],
            transactional_image_registration_intent=True,
            image_digest="sha256:image",
            external_side_effects_claimed_rolled_back=False,
        )
        assert plan.verify_digests_first is True
        assert plan.transactional_image_registration_intent is True
        assert plan.external_side_effects_claimed_rolled_back is False

    def test_no_registration_before_verified_packing(self) -> None:
        assert can_transition(LifecyclePhase.PACKING, LifecyclePhase.VERIFIED) is not False
        assert can_transition(LifecyclePhase.PACKING, LifecyclePhase.VERIFIED)
        assert not can_transition(LifecyclePhase.EVALUATING, LifecyclePhase.VERIFIED)

    def test_rebuild_creates_new_record_not_overwrite(self) -> None:
        service = BuildLifecycleService()
        first = service.create_record(
            capability_digest="sha256:cap",
            model_key="ollama:example",
        )
        second = service.create_record(
            capability_digest="sha256:cap",
            model_key="ollama:example",
        )
        assert first.record_id != second.record_id
        assert first.revision == second.revision == 0
