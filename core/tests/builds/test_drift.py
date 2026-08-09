"""Runtime/model drift blocking tests."""

from __future__ import annotations

import pytest

from zana_core.builds.service import BuildLifecycleService, StaleRuntimeOrModelError


class TestDriftBlocking:
    def test_stale_runtime_blocks_without_mutating(self) -> None:
        service = BuildLifecycleService()
        record = service.create_record(
            capability_digest="sha256:cap",
            model_key="ollama:example",
            model_identity_digest="sha256:model",
        )
        with pytest.raises(StaleRuntimeOrModelError):
            service.block_for_stale_runtime(
                record,
                expected_revision=0,
                reason="runtime disappeared before baseline",
            )
        assert record.revision == 0

    def test_model_identity_change_blocks_by_design(self) -> None:
        service = BuildLifecycleService()
        record = service.create_record(
            capability_digest="sha256:cap",
            model_key="ollama:example",
            model_identity_digest="sha256:old",
        )
        with pytest.raises(StaleRuntimeOrModelError):
            service.block_for_stale_runtime(
                record,
                expected_revision=0,
                reason="model identity changed to sha256:new",
            )
