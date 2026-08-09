"""Exact runtime/model identity selection tests."""

from __future__ import annotations

import pytest

from tests.instances.helpers import (
    IDENTITY_DIGEST,
    IMAGE_DIGEST,
    make_image_config,
    make_model,
    make_runtime,
)
from zana_core.instances.runtime_selection import (
    ModelCapabilityMismatchError,
    ModelIdentityMismatchError,
    RuntimeIncompatibleError,
    RuntimeSelectionService,
    RuntimeUnavailableError,
    WeakIdentityError,
)

SERVICE = RuntimeSelectionService()


def _select(config, runtimes=None, models=None):
    if runtimes is None:
        runtimes = [make_runtime()]
    return SERVICE.select(
        config=config,
        image_digest=IMAGE_DIGEST,
        instance_id="i1",
        expected_state_revision=0,
        runtimes=runtimes,
        models=models if models is not None else [make_model()],
    )


class TestExactIdentity:
    def test_exact_digest_match_produces_start_plan(self) -> None:
        plan = _select(make_image_config())
        assert plan.base_model_digest == IDENTITY_DIGEST
        assert plan.model_digest == IDENTITY_DIGEST
        assert plan.runtime_id == "ollama-local"

    def test_display_name_never_matches_without_digest(self) -> None:
        config = make_image_config()
        model = make_model()
        model = model.model_copy(update={"digest": "sha256:" + "7" * 64})
        with pytest.raises(ModelIdentityMismatchError, match="drift"):
            _select(config, models=[model])

    def test_same_display_name_different_digest_is_rejected(self) -> None:
        config = make_image_config()
        model = make_model()
        model = model.model_copy(
            update={
                "display_name": "example-model:tag",
                "digest": "sha256:" + "8" * 64,
            }
        )
        with pytest.raises(ModelIdentityMismatchError):
            _select(config, models=[model])

    def test_weak_identity_blocks_selection(self) -> None:
        with pytest.raises(WeakIdentityError):
            _select(make_image_config(identity_digest=None))


class TestRuntimeAvailability:
    def test_no_online_runtime_blocks_start(self) -> None:
        with pytest.raises(RuntimeUnavailableError):
            _select(make_image_config(), runtimes=[make_runtime(online=False)])

    def test_runtime_disappearance_blocks_start(self) -> None:
        try:
            _select(make_image_config(), runtimes=[])
            raise AssertionError("expected RuntimeUnavailableError")
        except RuntimeUnavailableError:
            pass

    def test_incompatible_runtime_blocks_start(self) -> None:
        config = make_image_config()
        config = config.model_copy(deep=True)
        config.base_model.runtime_compatibility = ["openai-compatible"]
        with pytest.raises(RuntimeIncompatibleError):
            _select(config)

    def test_required_capability_missing_blocks_start(self) -> None:
        config = make_image_config()
        config = config.model_copy(deep=True)
        config.base_model.required_capabilities = ["tool_use"]
        model = make_model()
        model = model.model_copy(update={"capabilities": ["completion"]})
        with pytest.raises(ModelCapabilityMismatchError):
            _select(config, models=[model])
