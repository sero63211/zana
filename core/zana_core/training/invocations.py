"""Deterministic MLX-LM training argv builders; this module never executes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from zana_core.training.contracts import (
    DatasetSplitManifest,
    InvocationSpec,
    TrainingRequestConfig,
    normalize_sha256,
)


def _config_digest(config: TrainingRequestConfig) -> str:
    payload = {
        "provider": config.provider,
        "source_digest": config.source.digest,
        "base": config.base.model_dump(mode="json"),
        "seed": config.seed,
        "iters": config.iters,
        "batch_size": config.batch_size,
        "learning_rate": config.learning_rate,
        "max_seq_length": config.max_seq_length,
        "num_layers": config.num_layers,
        "grad_checkpoint": config.grad_checkpoint,
        "train_digest": config.train_split.sha256,
        "validation_digest": config.validation_split.sha256 if config.validation_split else None,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _dataset_digest(config: TrainingRequestConfig) -> str:
    """Digest of train plus optional validation; held-out eval is excluded."""
    train = normalize_sha256(config.train_split.sha256)
    if config.validation_split is None:
        payload = f"train:{train}"
    else:
        valid = normalize_sha256(config.validation_split.sha256)
        payload = f"train:{train};valid:{valid}"
    return hashlib.sha256(payload.encode()).hexdigest()


def build_mlx_lm_invocation(
    config: TrainingRequestConfig,
    *,
    data_dir: Path,
    adapter_path: Path,
    provider_version: str,
    package_version: str,
) -> InvocationSpec:
    """Build the official ``mlx_lm.lora`` train argv as deterministic data."""
    if config.provider != "mlx_lm" or config.source.provider != "mlx_lm":
        raise ValueError("MLX-LM invocation requires the mlx_lm provider")
    if not data_dir.is_absolute() or not adapter_path.is_absolute():
        raise ValueError("staged data and adapter paths must be absolute")
    args = [
        "--model",
        str(config.source.path),
        "--train",
        "--data",
        str(data_dir),
        "--iters",
        str(config.iters),
        "--adapter-path",
        str(adapter_path),
        "--fine-tune-type",
        "lora",
        "--batch-size",
        str(config.batch_size),
        "--seed",
        str(config.seed),
    ]
    if config.learning_rate is not None:
        args += ["--learning-rate", str(config.learning_rate)]
    if config.max_seq_length is not None:
        args += ["--max-seq-length", str(config.max_seq_length)]
    if config.num_layers is not None:
        args += ["--num-layers", str(config.num_layers)]
    if config.grad_checkpoint:
        args += ["--grad-checkpoint"]
    return InvocationSpec(
        provider=config.provider,
        executable="mlx_lm.lora",
        args=tuple(args),
        env={"PATH": "/usr/bin:/bin"},
        provider_version=provider_version,
        package_version=package_version,
        seed=config.seed,
        dataset_digest=_dataset_digest(config),
        config_digest=_config_digest(config),
        output_path=adapter_path,
        environment_metadata={"provider": "mlx_lm"},
    )


def require_dataset_hashes(splits: list[DatasetSplitManifest]) -> None:
    """Fail closed when any configured split lacks a canonical digest."""
    missing = [split.role for split in splits if not split.sha256]
    if missing:
        raise ValueError(f"dataset split(s) without sha256: {', '.join(missing)}")
