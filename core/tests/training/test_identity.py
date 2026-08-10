"""Exact identity enforcement tests."""

from __future__ import annotations

import hashlib

import pytest

from zana_core.training.contracts import (
    CompatibilityDecision,
    InferenceIdentity,
    TrainingSourceIdentity,
)
from zana_core.training.identity import enforce_exact_base_identity


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _inference(digest_value: str | None) -> InferenceIdentity:
    return InferenceIdentity(
        runtime_id="ollama",
        model_id="model",
        display_name="model",
        digest=digest_value,
    )


def _source(digest_value: str, provider: str = "mlx_lm") -> TrainingSourceIdentity:
    return TrainingSourceIdentity(
        source_id="train-source",
        digest=digest_value,
        format="mlx",
        provider=provider,
    )


class TestExactIdentity:
    def test_display_name_never_establishes_trainable(self) -> None:
        decision, reason = enforce_exact_base_identity(
            _inference(None),
            _source(digest("abc")),
            "0.1.0",
        )
        assert decision == CompatibilityDecision.NOT_TRAINABLE
        assert "exact digest" in reason

    def test_exact_match_is_trainable(self) -> None:
        decision, _ = enforce_exact_base_identity(
            _inference(digest("abc")),
            _source(digest("abc")),
            "0.1.0",
        )
        assert decision == CompatibilityDecision.TRAINABLE

    def test_digest_mismatch_is_not_trainable(self) -> None:
        decision, reason = enforce_exact_base_identity(
            _inference(digest("abc")),
            _source(digest("def")),
            "0.1.0",
        )
        assert decision == CompatibilityDecision.NOT_TRAINABLE
        assert "differs" in reason

    def test_missing_provider_version_is_not_trainable(self) -> None:
        decision, _ = enforce_exact_base_identity(
            _inference(digest("abc")),
            _source(digest("abc")),
            "",
        )
        assert decision == CompatibilityDecision.NOT_TRAINABLE

    def test_unknown_provider_is_not_trainable(self) -> None:
        decision, _ = enforce_exact_base_identity(
            _inference(digest("abc")),
            _source(digest("abc"), provider="unknown"),
            "0.1.0",
        )
        assert decision == CompatibilityDecision.NOT_TRAINABLE

    def test_hf_peft_is_not_trainable_in_v1(self) -> None:
        decision, reason = enforce_exact_base_identity(
            _inference(digest("abc")),
            _source(digest("abc"), provider="hf_peft"),
            "0.10.0",
        )
        assert decision == CompatibilityDecision.NOT_TRAINABLE
        assert "unsupported" in reason

    def test_contract_rejects_bad_digest_form(self) -> None:
        with pytest.raises(ValueError):
            _inference("sha256:abc")
        with pytest.raises(ValueError):
            _source("not-a-digest")
