"""Exact identity enforcement tests."""

from __future__ import annotations

from zana_core.training.contracts import (
    CompatibilityDecision,
    InferenceIdentity,
    TrainingSourceIdentity,
)
from zana_core.training.identity import enforce_exact_base_identity


def _inference(digest: str | None) -> InferenceIdentity:
    return InferenceIdentity(
        runtime_id="ollama",
        model_id="model",
        display_name="model",
        digest=digest,
    )


def _source(digest: str, provider: str = "mlx_lm") -> TrainingSourceIdentity:
    return TrainingSourceIdentity(
        source_id="train-source",
        digest=digest,
        format="mlx",
        provider=provider,
    )


class TestExactIdentity:
    def test_display_name_never_establishes_trainable(self) -> None:
        decision, reason = enforce_exact_base_identity(
            _inference(None),
            _source("sha256:abc"),
            "0.1.0",
        )
        assert decision == CompatibilityDecision.NOT_TRAINABLE
        assert "exact digest" in reason

    def test_exact_match_is_trainable(self) -> None:
        decision, _ = enforce_exact_base_identity(
            _inference("sha256:abc"),
            _source("sha256:abc"),
            "0.1.0",
        )
        assert decision == CompatibilityDecision.TRAINABLE

    def test_digest_mismatch_is_not_trainable(self) -> None:
        decision, reason = enforce_exact_base_identity(
            _inference("sha256:abc"),
            _source("sha256:def"),
            "0.1.0",
        )
        assert decision == CompatibilityDecision.NOT_TRAINABLE
        assert "differs" in reason

    def test_missing_provider_version_is_not_trainable(self) -> None:
        decision, _ = enforce_exact_base_identity(
            _inference("sha256:abc"),
            _source("sha256:abc"),
            "",
        )
        assert decision == CompatibilityDecision.NOT_TRAINABLE

    def test_unknown_provider_is_not_trainable(self) -> None:
        decision, _ = enforce_exact_base_identity(
            _inference("sha256:abc"),
            _source("sha256:abc", provider="unknown"),
            "0.1.0",
        )
        assert decision == CompatibilityDecision.NOT_TRAINABLE
