"""Invocation builder tests: official argv, allowlisting, and digest separation."""

from __future__ import annotations

import hashlib
from pathlib import Path

from zana_core.training.contracts import (
    AdapterBaseIdentity,
    DatasetSplitManifest,
    LocalTrainingSource,
    TrainingRequestConfig,
)
from zana_core.training.invocations import (
    build_mlx_lm_invocation,
    require_dataset_hashes,
)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _source() -> LocalTrainingSource:
    return LocalTrainingSource(
        source_id="source-1",
        path=Path("/local/model/mlx-model"),
        digest=digest("base"),
        format="mlx",
        provider="mlx_lm",
    )


def _config() -> TrainingRequestConfig:
    base = AdapterBaseIdentity(
        base_model_digest=digest("base"),
        training_source_digest=digest("base"),
        training_source_provider="mlx_lm",
        provider_version="0.5.0",
    )
    return TrainingRequestConfig(
        provider="mlx_lm",
        source=_source(),
        base=base,
        train_split=DatasetSplitManifest(
            role="train",
            path=Path("/data/train.jsonl"),
            sha256=digest("train"),
            size_bytes=1,
            record_ids=("train-1",),
        ),
        validation_split=DatasetSplitManifest(
            role="validation",
            path=Path("/data/valid.jsonl"),
            sha256=digest("val"),
            size_bytes=1,
            record_ids=("val-1",),
        ),
        eval_split=DatasetSplitManifest(
            role="evaluation",
            path=Path("/data/eval.jsonl"),
            sha256=digest("eval"),
            size_bytes=1,
            record_ids=("eval-1",),
        ),
        seed=7,
        iters=3,
        batch_size=2,
        learning_rate=1e-4,
        max_seq_length=512,
        num_layers=4,
        grad_checkpoint=True,
    )


class TestMlxLmInvocationBuilder:
    def test_official_train_argv_is_exact_and_deterministic(self) -> None:
        spec = build_mlx_lm_invocation(
            _config(),
            data_dir=Path("/workspace/data"),
            adapter_path=Path("/workspace/out"),
            provider_version="0.5.0",
            package_version="0.5.0",
        )
        assert spec.executable == "mlx_lm.lora"
        assert spec.args == (
            "--model",
            "/local/model/mlx-model",
            "--train",
            "--data",
            "/workspace/data",
            "--iters",
            "3",
            "--adapter-path",
            "/workspace/out",
            "--fine-tune-type",
            "lora",
            "--batch-size",
            "2",
            "--seed",
            "7",
            "--learning-rate",
            "0.0001",
            "--max-seq-length",
            "512",
            "--num-layers",
            "4",
            "--grad-checkpoint",
        )
        again = build_mlx_lm_invocation(
            _config(),
            data_dir=Path("/workspace/data"),
            adapter_path=Path("/workspace/out"),
            provider_version="0.5.0",
            package_version="0.5.0",
        )
        assert again.args == spec.args
        assert again.config_digest == spec.config_digest

    def test_invented_flags_and_remote_code_are_absent(self) -> None:
        spec = build_mlx_lm_invocation(
            _config(),
            data_dir=Path("/workspace/data"),
            adapter_path=Path("/workspace/out"),
            provider_version="0.5.0",
            package_version="0.5.0",
        )
        forbidden = {"--val-file", "--output", "--max-steps", "--max-tokens", "--trust-remote-code"}
        assert forbidden.isdisjoint(spec.args)

    def test_local_path_used_and_digest_never_a_path(self) -> None:
        spec = build_mlx_lm_invocation(
            _config(),
            data_dir=Path("/workspace/data"),
            adapter_path=Path("/workspace/out"),
            provider_version="0.5.0",
            package_version="0.5.0",
        )
        assert "--model" in spec.args
        assert "/local/model/mlx-model" in spec.args
        assert digest("base") not in spec.args
        assert digest("train") not in spec.args
        assert spec.seed == 7
        assert spec.output_path == Path("/workspace/out")

    def test_dataset_digest_covers_train_and_validation_not_eval(self) -> None:
        spec = build_mlx_lm_invocation(
            _config(),
            data_dir=Path("/workspace/data"),
            adapter_path=Path("/workspace/out"),
            provider_version="0.5.0",
            package_version="0.5.0",
        )
        assert spec.dataset_digest != digest("train")
        expected = hashlib.sha256(
            f"train:{digest('train')};valid:{digest('val')}".encode()
        ).hexdigest()
        assert spec.dataset_digest == expected
        changed_eval = _config().model_copy(
            update={
                "eval_split": _config().eval_split.model_copy(
                    update={"sha256": digest("other-eval")}
                )
            }
        )
        changed = build_mlx_lm_invocation(
            changed_eval,
            data_dir=Path("/workspace/data"),
            adapter_path=Path("/workspace/out"),
            provider_version="0.5.0",
            package_version="0.5.0",
        )
        assert changed.dataset_digest == spec.dataset_digest
        changed_valid = _config().model_copy(
            update={
                "validation_split": _config().validation_split.model_copy(
                    update={"sha256": digest("other-valid")}
                )
            }
        )
        changed = build_mlx_lm_invocation(
            changed_valid,
            data_dir=Path("/workspace/data"),
            adapter_path=Path("/workspace/out"),
            provider_version="0.5.0",
            package_version="0.5.0",
        )
        assert changed.dataset_digest != spec.dataset_digest

    def test_non_mlx_provider_is_rejected(self) -> None:
        try:
            build_mlx_lm_invocation(
                _config().model_copy(update={"provider": "hf_peft"}),
                data_dir=Path("/workspace/data"),
                adapter_path=Path("/workspace/out"),
                provider_version="0.1.0",
                package_version="0.1.0",
            )
        except ValueError as error:
            assert "mlx_lm" in str(error)
        else:
            raise AssertionError("expected non-MLX provider rejection")

    def test_relative_private_paths_are_rejected(self) -> None:
        try:
            build_mlx_lm_invocation(
                _config(),
                data_dir=Path("relative/data"),
                adapter_path=Path("relative/out"),
                provider_version="0.5.0",
                package_version="0.5.0",
            )
        except ValueError as error:
            assert "absolute" in str(error)
        else:
            raise AssertionError("expected relative path rejection")

    def test_require_dataset_hashes_fails_closed(self) -> None:
        try:
            DatasetSplitManifest(
                role="train",
                path=Path("/data/train.jsonl"),
                sha256="",
                size_bytes=1,
            )
        except ValueError as error:
            assert "sha256" in str(error)
        else:
            raise AssertionError("expected empty hash contract rejection")
        valid = DatasetSplitManifest(
            role="train",
            path=Path("/data/train.jsonl"),
            sha256=digest("train"),
            size_bytes=1,
        )
        require_dataset_hashes([valid])
