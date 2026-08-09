"""Adapter provenance and digest verification with tiny temporary fixtures."""

from __future__ import annotations

from pathlib import Path

from zana_core.training.adapters import validate_adapter
from zana_core.training.contracts import AdapterBaseIdentity


def _base() -> AdapterBaseIdentity:
    return AdapterBaseIdentity(
        base_model_digest="sha256:base",
        training_source_digest="sha256:base",
        training_source_provider="mlx_lm",
        provider_version="0.5.0",
    )


class TestAdapterValidation:
    def test_safetensors_digest_and_provenance(self, tmp_path: Path) -> None:
        path = tmp_path / "adapter.safetensors"
        path.write_bytes(b"fake-adapter-bytes")
        validation, metadata = validate_adapter(
            path=path,
            base=_base(),
            provider="mlx_lm",
            dataset_digest="sha256:train",
            config_digest="sha256:config",
            provider_version="0.5.0",
        )
        assert validation.ok is True
        assert metadata is not None
        assert metadata.adapter_digest == validation.digest
        assert metadata.base_model_digest == "sha256:base"
        assert metadata.training_provider == "mlx_lm"

    def test_wrong_extension_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "adapter.bin"
        path.write_bytes(b"x")
        validation, _ = validate_adapter(
            path=path,
            base=_base(),
            provider="mlx_lm",
            dataset_digest="sha256:train",
            config_digest="sha256:config",
            provider_version="0.5.0",
        )
        assert validation.ok is False
        assert "safetensors" in validation.reason

    def test_base_digest_mismatch_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "adapter.safetensors"
        path.write_bytes(b"x")
        base = _base().model_copy(update={"training_source_digest": "sha256:other"})
        validation, _ = validate_adapter(
            path=path,
            base=base,
            provider="mlx_lm",
            dataset_digest="sha256:train",
            config_digest="sha256:config",
            provider_version="0.5.0",
        )
        assert validation.ok is False

    def test_missing_provenance_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "adapter.safetensors"
        path.write_bytes(b"x")
        validation, _ = validate_adapter(
            path=path,
            base=_base(),
            provider="mlx_lm",
            dataset_digest="",
            config_digest="",
            provider_version="",
        )
        assert validation.ok is False

    def test_unreadable_file_rejected(self, tmp_path: Path) -> None:
        validation, _ = validate_adapter(
            path=tmp_path / "missing.safetensors",
            base=_base(),
            provider="mlx_lm",
            dataset_digest="sha256:train",
            config_digest="sha256:config",
            provider_version="0.5.0",
        )
        assert validation.ok is False
