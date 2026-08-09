"""Invocation spec builder tests: allowlisting and argv data-only output."""

from __future__ import annotations

from pathlib import Path

from zana_core.training.contracts import (
    AdapterBaseIdentity,
    DatasetSplitManifest,
    TrainingRequestConfig,
)
from zana_core.training.invocations import (
    build_hf_peft_invocation,
    build_mlx_lm_invocation,
    require_dataset_hashes,
)


def _config() -> TrainingRequestConfig:
    base = AdapterBaseIdentity(
        base_model_digest="sha256:base",
        training_source_digest="sha256:base",
        training_source_provider="mlx_lm",
        provider_version="0.5.0",
    )
    return TrainingRequestConfig(
        provider="mlx_lm",
        base=base,
        train_split=DatasetSplitManifest(
            role="train",
            path=Path("/data/train.jsonl"),
            sha256="sha256:train",
            size_bytes=1,
            record_ids=("train-1",),
        ),
        validation_split=DatasetSplitManifest(
            role="validation",
            path=Path("/data/val.jsonl"),
            sha256="sha256:val",
            size_bytes=1,
            record_ids=("val-1",),
        ),
        seed=7,
        max_steps=3,
        max_tokens=128,
        learning_rate=1e-4,
        output_path=Path("/data/out/adapter.safetensors"),
    )


class TestInvocationBuilders:
    def test_mlx_args_are_allowlisted_and_data_only(self) -> None:
        spec = build_mlx_lm_invocation(
            _config(),
            provider_version="0.5.0",
            package_version="0.5.0",
        )
        assert spec.executable == "mlx_lm.train"
        assert "--model" in spec.args
        assert "--seed" in spec.args
        assert "--val-file" in spec.args
        assert spec.seed == 7
        assert spec.dataset_digest == "sha256:train"
        assert spec.config_digest
        assert spec.output_path == Path("/data/out/adapter.safetensors")

    def test_hf_peft_args_are_allowlisted(self) -> None:
        spec = build_hf_peft_invocation(
            _config().model_copy(update={"provider": "hf_peft"}),
            provider_version="0.10.0",
            package_version="0.10.0",
        )
        assert spec.executable == "python"
        assert "--base_model" in spec.args
        assert "--validation_file" in spec.args
        assert "--seed" in spec.args
        assert spec.provider == "hf_peft"

    def test_require_dataset_hashes_fails_closed(self) -> None:
        split = DatasetSplitManifest(
            role="train",
            path=Path("/data/train.jsonl"),
            sha256="",
            size_bytes=1,
        )
        try:
            require_dataset_hashes([split])
        except ValueError as error:
            assert "sha256" in str(error)
        else:
            raise AssertionError("expected missing hash rejection")
