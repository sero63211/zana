"""Exact training/adapter base identity enforcement."""

from __future__ import annotations

from zana_core.training.contracts import (
    AdapterBaseIdentity,
    CompatibilityDecision,
    InferenceIdentity,
    TrainingSourceIdentity,
)


def enforce_exact_base_identity(
    inference: InferenceIdentity,
    training_source: TrainingSourceIdentity,
    provider_version: str,
) -> tuple[CompatibilityDecision, str]:
    """Return trainable only when exact digests/providers prove compatibility.

    A runtime display name never establishes a trainable base. If either
    identity is missing or mismatched the result is NOT_TRAINABLE.
    """
    if not inference.digest:
        return CompatibilityDecision.NOT_TRAINABLE, "inference identity has no exact digest"
    if not training_source.digest:
        return CompatibilityDecision.NOT_TRAINABLE, "training source identity has no exact digest"
    if training_source.digest != inference.digest:
        return (
            CompatibilityDecision.NOT_TRAINABLE,
            "training source digest differs from inference digest",
        )
    if not provider_version:
        return CompatibilityDecision.NOT_TRAINABLE, "provider version is required"
    if training_source.provider != "mlx_lm":
        return (
            CompatibilityDecision.NOT_TRAINABLE,
            f"unsupported training provider {training_source.provider!r}",
        )
    return (
        CompatibilityDecision.TRAINABLE,
        "exact inference and training source digests match with a known provider",
    )


def make_adapter_base_identity(
    inference: InferenceIdentity,
    training_source: TrainingSourceIdentity,
    provider_version: str,
) -> AdapterBaseIdentity | None:
    """Build an adapter base identity only when exact compatibility is proven."""
    decision, _ = enforce_exact_base_identity(inference, training_source, provider_version)
    if decision != CompatibilityDecision.TRAINABLE:
        return None
    return AdapterBaseIdentity(
        base_model_digest=inference.digest or "",
        training_source_digest=training_source.digest,
        training_source_provider=training_source.provider,
        provider_version=provider_version,
    )
