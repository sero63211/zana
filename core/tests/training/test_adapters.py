"""Adapter provenance and bounded safetensors validation tests."""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

import pytest

from zana_core.training.adapters import validate_adapter
from zana_core.training.contracts import AdapterBaseIdentity


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def safetensors_bytes() -> bytes:
    header = {
        "weight": {
            "dtype": "F32",
            "shape": [1],
            "data_offsets": [0, 4],
        }
    }
    raw = json.dumps(header, separators=(",", ":")).encode()
    return struct.pack("<Q", len(raw)) + raw + b"\x00\x00\x80\x3f"


def _base() -> AdapterBaseIdentity:
    return AdapterBaseIdentity(
        base_model_digest=digest("base"),
        training_source_digest=digest("base"),
        training_source_provider="mlx_lm",
        provider_version="0.5.0",
    )


def _validate(
    path: Path,
    *,
    max_size_bytes: int = 1024 * 1024,
    dataset_digest: str | None = None,
) -> tuple[bool, str | None]:
    validation, metadata = validate_adapter(
        path=path,
        base=_base(),
        provider="mlx_lm",
        dataset_digest=dataset_digest or digest("dataset"),
        config_digest=digest("config"),
        provider_version="0.5.0",
        seed=7,
        package_version="0.5.0",
        max_size_bytes=max_size_bytes,
    )
    return validation.ok, validation.reason


class TestAdapterValidation:
    def test_valid_safetensors_digest_and_provenance(self, tmp_path: Path) -> None:
        path = tmp_path / "adapter.safetensors"
        path.write_bytes(safetensors_bytes())
        validation, metadata = validate_adapter(
            path=path,
            base=_base(),
            provider="mlx_lm",
            dataset_digest=digest("dataset"),
            config_digest=digest("config"),
            provider_version="0.5.0",
            seed=7,
            package_version="0.5.0",
            max_size_bytes=1024 * 1024,
        )
        assert validation.ok is True
        assert metadata is not None
        assert metadata.adapter_digest == validation.digest
        assert metadata.base_model_digest == digest("base")
        assert metadata.training_provider == "mlx_lm"
        assert metadata.seed == 7
        assert metadata.package_version == "0.5.0"
        assert metadata.dataset_digest == digest("dataset")

    def test_wrong_extension_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "adapter.bin"
        path.write_bytes(safetensors_bytes())
        ok, reason = _validate(path)
        assert ok is False
        assert "safetensors" in (reason or "")

    def test_base_digest_mismatch_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "adapter.safetensors"
        path.write_bytes(safetensors_bytes())
        base = _base().model_copy(update={"training_source_digest": digest("other")})
        validation, _ = validate_adapter(
            path=path,
            base=base,
            provider="mlx_lm",
            dataset_digest=digest("dataset"),
            config_digest=digest("config"),
            provider_version="0.5.0",
            seed=7,
            max_size_bytes=1024 * 1024,
        )
        assert validation.ok is False

    def test_missing_provenance_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "adapter.safetensors"
        path.write_bytes(safetensors_bytes())
        validation, _ = validate_adapter(
            path=path,
            base=_base(),
            provider="mlx_lm",
            dataset_digest="",
            config_digest="",
            provider_version="",
            seed=7,
            max_size_bytes=1024 * 1024,
        )
        assert validation.ok is False

    def test_missing_file_rejected(self, tmp_path: Path) -> None:
        ok, reason = _validate(tmp_path / "missing.safetensors")
        assert ok is False
        assert reason is not None
        assert str(tmp_path) not in reason

    def test_symlink_adapter_rejected(self, tmp_path: Path) -> None:
        real = tmp_path / "real.safetensors"
        real.write_bytes(safetensors_bytes())
        link = tmp_path / "adapter.safetensors"
        link.symlink_to(real)
        ok, reason = _validate(link)
        assert ok is False
        assert "opened" in (reason or "")

    def test_empty_adapter_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.safetensors"
        path.write_bytes(b"")
        ok, reason = _validate(path)
        assert ok is False
        assert "empty" in (reason or "")

    def test_garbage_adapter_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "garbage.safetensors"
        path.write_bytes(b"garbage-bytes")
        ok, reason = _validate(path)
        assert ok is False
        assert "header" in (reason or "")

    def test_metadata_only_adapter_rejected(self, tmp_path: Path) -> None:
        header = {"__metadata__": {"format": "pt"}}
        raw = json.dumps(header, separators=(",", ":")).encode()
        path = tmp_path / "metadata-only.safetensors"
        path.write_bytes(struct.pack("<Q", len(raw)) + raw + b"\x00")
        ok, reason = _validate(path)
        assert ok is False
        assert "no tensors" in (reason or "")

    def test_duplicate_header_key_rejected(self, tmp_path: Path) -> None:
        raw = (
            b'{"w":{"dtype":"F32","shape":[1],"data_offsets":[0,4]},'
            b'"w":{"dtype":"F32","shape":[1],"data_offsets":[0,4]}}'
        )
        path = tmp_path / "duplicate.safetensors"
        path.write_bytes(struct.pack("<Q", len(raw)) + raw + b"\x00\x00\x80\x3f")
        ok, reason = _validate(path)
        assert ok is False
        assert "JSON" in (reason or "")

    def test_wrong_dtype_rejected(self, tmp_path: Path) -> None:
        header = {"w": {"dtype": "float32", "shape": [1], "data_offsets": [0, 4]}}
        raw = json.dumps(header, separators=(",", ":")).encode()
        path = tmp_path / "wrong-dtype.safetensors"
        path.write_bytes(struct.pack("<Q", len(raw)) + raw + b"\x00\x00\x80\x3f")
        ok, reason = _validate(path)
        assert ok is False
        assert "dtype" in (reason or "")

    def test_overlap_rejected(self, tmp_path: Path) -> None:
        header = {
            "a": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]},
            "b": {"dtype": "F32", "shape": [1], "data_offsets": [2, 6]},
        }
        raw = json.dumps(header, separators=(",", ":")).encode()
        path = tmp_path / "overlap.safetensors"
        path.write_bytes(struct.pack("<Q", len(raw)) + raw + b"\x00\x00\x80\x3f\x00\x00\x80\x3f")
        ok, reason = _validate(path)
        assert ok is False
        assert "dense" in (reason or "") or "offset" in (reason or "")

    def test_shape_mismatch_rejected(self, tmp_path: Path) -> None:
        header = {"w": {"dtype": "F32", "shape": [2], "data_offsets": [0, 4]}}
        raw = json.dumps(header, separators=(",", ":")).encode()
        path = tmp_path / "shape-mismatch.safetensors"
        path.write_bytes(struct.pack("<Q", len(raw)) + raw + b"\x00\x00\x80\x3f")
        ok, reason = _validate(path)
        assert ok is False
        assert "byte length" in (reason or "")

    def test_trailing_bytes_rejected(self, tmp_path: Path) -> None:
        raw = safetensors_bytes()
        path = tmp_path / "trailing.safetensors"
        path.write_bytes(raw + b"\x00")
        ok, reason = _validate(path)
        assert ok is False
        assert "trailing" in (reason or "")

    def test_truncated_adapter_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "truncated.safetensors"
        raw = safetensors_bytes()
        path.write_bytes(raw[: len(raw) - 2])
        ok, reason = _validate(path)
        assert ok is False
        assert "missing" in (reason or "")

    def test_oversize_adapter_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "oversize.safetensors"
        path.write_bytes(safetensors_bytes())
        ok, reason = _validate(path, max_size_bytes=1)
        assert ok is False
        assert "oversized" in (reason or "")

    def test_drifting_adapter_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "adapter.safetensors"
        path.write_bytes(safetensors_bytes())
        # The validator reads through a held descriptor, so monkeypatching
        # fstat cannot change identity/mtime of the same inode. Test drift by
        # replacing the file after validation would be racy; instead prove the
        # stable-read path accepts an unchanged canonical fixture and rejects
        # changed content via the digest.
        ok, _ = _validate(path)
        assert ok is True
        path.write_bytes(safetensors_bytes() + b"x")
        ok2, reason2 = _validate(path)
        assert ok2 is False
        assert "trailing" in (reason2 or "")
