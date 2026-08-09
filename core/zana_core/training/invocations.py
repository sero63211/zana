"""Safe invocation specification builders returning argv data only."""

from __future__ import annotations

import hashlib
import json

from zana_core.training.contracts import (
    DatasetSplitManifest,
    InvocationSpec,
    TrainingRequestConfig,
)


def _config_digest(config: TrainingRequestConfig) -> str:
    payload = {
        "provider": config.provider,
        "base": config.base.model_dump(mode="json"),
        "seed": config.seed,
        "max_tokens": config.max_tokens,
        "max_steps": config.max_steps,
        "learning_rate": config.learning_rate,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def build_mlx_lm_invocation(
    config: TrainingRequestConfig,
    *,
    provider_version: str,
    package_version: str,
) -> InvocationSpec:
    """Build an MLX-LM train command as data; never executes it."""
    args: list[str] = [
        "--model",
        str(config.base.training_source_digest),
        "--train",
        str(config.train_split.path),
        "--seed",
        str(config.seed),
        "--output",
        str(config.output_path),
        "--batch-size",
        "1",
    ]
    if config.validation_split is not None:
        args += ["--val-file", str(config.validation_split.path)]
    if config.max_steps is not None:
        args += ["--max-steps", str(config.max_steps)]
    if config.max_tokens is not None:
        args += ["--max-tokens", str(config.max_tokens)]
    if config.learning_rate is not None:
        args += ["--learning-rate", str(config.learning_rate)]
    return InvocationSpec(
        provider=config.provider,
        executable="mlx_lm.train",
        args=tuple(args),
        env={"ZANA_TRAINING_MODE": "isolated"},
        provider_version=provider_version,
        package_version=package_version,
        seed=config.seed,
        dataset_digest=config.train_split.sha256,
        config_digest=_config_digest(config),
        output_path=config.output_path,
        environment_metadata={"provider": "mlx_lm"},
    )


def build_hf_peft_invocation(
    config: TrainingRequestConfig,
    *,
    provider_version: str,
    package_version: str,
) -> InvocationSpec:
    """Build an HF PEFT train command as data; never executes it."""
    args: list[str] = [
        "--base_model",
        str(config.base.training_source_digest),
        "--train_file",
        str(config.train_split.path),
        "--output_dir",
        str(config.output_path),
        "--seed",
        str(config.seed),
    ]
    if config.validation_split is not None:
        args += ["--validation_file", str(config.validation_split.path)]
    if config.max_steps is not None:
        args += ["--max_steps", str(config.max_steps)]
    if config.learning_rate is not None:
        args += ["--learning_rate", str(config.learning_rate)]
    return InvocationSpec(
        provider=config.provider,
        executable="python",
        args=tuple(args),
        env={"ZANA_TRAINING_MODE": "isolated"},
        provider_version=provider_version,
        package_version=package_version,
        seed=config.seed,
        dataset_digest=config.train_split.sha256,
        config_digest=_config_digest(config),
        output_path=config.output_path,
        environment_metadata={"provider": "hf_peft"},
    )


def require_dataset_hashes(splits: list[DatasetSplitManifest]) -> None:
    """Fail closed when any configured split lacks a canonical digest."""
    missing = [split.role for split in splits if not split.sha256]
    if missing:
        raise ValueError(f"dataset split(s) without sha256: {', '.join(missing)}")
