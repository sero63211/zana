"""Runtime materialization compatibility decision tests."""

from __future__ import annotations

from zana_core.training.contracts import (
    AdapterBaseIdentity,
    AdapterMetadata,
    AdapterState,
)
from zana_core.training.materialization import decide_materialization


def _metadata(provider: str = "mlx_lm") -> AdapterMetadata:
    return AdapterMetadata(
        base_model_digest="sha256:base",
        training_provider=provider,
        training_provider_version="0.5.0",
        dataset_digest="sha256:train",
        config_digest="sha256:config",
        adapter_digest="sha256:adapter",
        seed=7,
        state=AdapterState.COMPLETE,
    )


class TestMaterialization:
    def test_compatible_mlx_runtime(self) -> None:
        base = AdapterBaseIdentity(
            base_model_digest="sha256:base",
            training_source_digest="sha256:base",
            training_source_provider="mlx_lm",
            provider_version="0.5.0",
        )
        result = decide_materialization("mlx_lm", base, _metadata())
        assert result.compatible is True

    def test_base_mismatch_incompatible(self) -> None:
        base = AdapterBaseIdentity(
            base_model_digest="sha256:other",
            training_source_digest="sha256:other",
            training_source_provider="mlx_lm",
            provider_version="0.5.0",
        )
        result = decide_materialization("mlx_lm", base, _metadata())
        assert result.compatible is False
        assert "base digest" in result.reason

    def test_hf_peft_not_materializable_on_ollama(self) -> None:
        base = AdapterBaseIdentity(
            base_model_digest="sha256:base",
            training_source_digest="sha256:base",
            training_source_provider="hf_peft",
            provider_version="0.10.0",
        )
        result = decide_materialization("ollama", base, _metadata(provider="hf_peft"))
        assert result.compatible is False

    def test_unknown_runtime_incompatible(self) -> None:
        base = AdapterBaseIdentity(
            base_model_digest="sha256:base",
            training_source_digest="sha256:base",
            training_source_provider="mlx_lm",
            provider_version="0.5.0",
        )
        result = decide_materialization("unknown-runtime", base, _metadata())
        assert result.compatible is False
