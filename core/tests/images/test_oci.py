"""Deterministic OCI layout assembly, corruption, and validation tests."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from zana_core.artifacts.digest import digest_bytes
from zana_core.images.models import BaseModelReference, ZanaImageConfig
from zana_core.images.oci import (
    MAX_LAYERS,
    Descriptor,
    Index,
    Manifest,
    OciValidationError,
    _verify_existing_blob,
    assemble_oci_layout,
    canonical_json_bytes,
    validate_oci_layout,
)

BASE_DIGEST = "sha256:" + "b" * 64


def make_config() -> ZanaImageConfig:
    return ZanaImageConfig(
        name="policy-assistant",
        version="1.0.0",
        base_model=BaseModelReference(
            display_name="example-model",
            identity_digest=BASE_DIGEST,
        ),
    )


@pytest.fixture
def assembled(tmp_path: Path) -> tuple[ZanaImageConfig, Path, Path]:
    config = make_config()
    behavior = tmp_path / "behavior.json"
    behavior.write_bytes(b'{"policy": "helpful"}')
    root = tmp_path / "layout"
    root.mkdir()
    assemble_oci_layout(config, {"behavior": behavior}, root)
    return config, root, behavior


class TestDeterministicAssembly:
    def test_layout_has_standard_files_and_blob_paths(
        self,
        assembled: tuple[ZanaImageConfig, Path, Path],
    ) -> None:
        _, root, _ = assembled
        assert (root / "oci-layout").is_file()
        assert (root / "index.json").is_file()
        assert (root / "manifest.json").is_file()
        assert (root / "blobs" / "sha256").is_dir()

    def test_deterministic_digests(self, tmp_path: Path) -> None:
        config = make_config()
        behavior = tmp_path / "behavior.json"
        behavior.write_bytes(b"same")
        first_root = tmp_path / "first"
        second_root = tmp_path / "second"
        first_root.mkdir()
        second_root.mkdir()
        first = assemble_oci_layout(config, {"behavior": behavior}, first_root)
        second = assemble_oci_layout(config, {"behavior": behavior}, second_root)
        assert first.image_digest == second.image_digest
        assert first.manifest_digest == second.manifest_digest
        assert first.blob_digests == second.blob_digests

    def test_canonical_serialization_is_sorted_and_compact(self) -> None:
        encoded = canonical_json_bytes({"z": 1, "a": [2, 1], "b": True})
        assert encoded == b'{"a":[2,1],"b":true,"z":1}'

    def test_unknown_blob_role_is_rejected(self, tmp_path: Path) -> None:
        blob = tmp_path / "x.bin"
        blob.write_bytes(b"x")
        root = tmp_path / "layout"
        root.mkdir()
        with pytest.raises(OciValidationError):
            assemble_oci_layout(make_config(), {"instances/chat.json": blob}, root)

    def test_blob_is_streamed_once_in_bounded_chunks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os as os_module

        from zana_core.images import oci as oci_module

        source = tmp_path / "big.bin"
        data = b"z" * 300_000
        source.write_bytes(data)
        root = tmp_path / "layout"
        root.mkdir()
        reads: list[int] = []
        real_read = os_module.read
        source_ino = source.stat().st_ino

        def bounded_read(fd: int, size: int) -> bytes:
            info = os_module.fstat(fd)
            if info.st_ino == source_ino:
                if size < 0 or size > 64 * 1024:
                    raise AssertionError(f"unbounded read requested: {size}")
                chunk = real_read(fd, size)
                reads.append(len(chunk))
                return chunk
            return real_read(fd, size)

        monkeypatch.setattr(oci_module.os, "read", bounded_read)

        def no_read_bytes(*args: object, **kwargs: object) -> bytes:
            raise AssertionError("assemble_oci_layout must not call Path.read_bytes")

        monkeypatch.setattr(Path, "read_bytes", no_read_bytes)
        result = assemble_oci_layout(
            make_config(), {"behavior": source}, root, chunk_size=64 * 1024
        )
        assert len(reads) >= 5
        assert all(size <= 64 * 1024 for size in reads)
        blob_path = root / "blobs" / "sha256" / result.blob_digests[0].removeprefix("sha256:")
        with blob_path.open("rb") as handle:
            assert handle.read() == data
        assert not list((root / "blobs" / "sha256").glob(".tmp-*"))

    def test_blob_size_mismatch_is_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os as os_module

        from zana_core.images import oci as oci_module

        source = tmp_path / "shrink.bin"
        source.write_bytes(b"x" * 4096)
        root = tmp_path / "layout"
        root.mkdir()
        real_read = os_module.read
        source_ino = source.stat().st_ino

        def truncating_read(fd: int, size: int) -> bytes:
            info = os_module.fstat(fd)
            if info.st_ino == source_ino:
                real_read(fd, size)
                return b""
            return real_read(fd, size)

        monkeypatch.setattr(oci_module.os, "read", truncating_read)
        monkeypatch.setattr(
            Path,
            "read_bytes",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("read_bytes must not be used")
            ),
        )
        with pytest.raises(OciValidationError, match="changed size"):
            assemble_oci_layout(make_config(), {"behavior": source}, root)
        assert not list((root / "blobs" / "sha256").glob(".tmp-*"))

    def test_partial_blob_copy_cleans_up_on_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os as os_module

        from zana_core.images import oci as oci_module

        source = tmp_path / "fail.bin"
        source.write_bytes(b"x" * 100_000)
        root = tmp_path / "layout"
        root.mkdir()
        real_read = os_module.read
        source_ino = source.stat().st_ino
        emitted: set[int] = set()

        def failing_read(fd: int, size: int) -> bytes:
            info = os_module.fstat(fd)
            if info.st_ino == source_ino:
                if fd in emitted:
                    raise OSError("simulated failure")
                emitted.add(fd)
                return real_read(fd, size)
            return real_read(fd, size)

        monkeypatch.setattr(oci_module.os, "read", failing_read)
        with pytest.raises(OSError, match="simulated failure"):
            assemble_oci_layout(make_config(), {"behavior": source}, root)
        blob_dir = root / "blobs" / "sha256"
        assert not list(blob_dir.glob(".tmp-*"))
        assert list(blob_dir.iterdir()) == []

    def test_blob_copy_deadline_cleans_up(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "slow.bin"
        source.write_bytes(b"x" * 4096)
        root = tmp_path / "layout"
        root.mkdir()
        monkeypatch.setattr(
            Path,
            "read_bytes",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("read_bytes must not be used")
            ),
        )
        with pytest.raises(OciValidationError, match="deadline"):
            assemble_oci_layout(
                make_config(),
                {"behavior": source},
                root,
                deadline_seconds=-1.0,
            )
        blob_dir = root / "blobs" / "sha256"
        assert not list(blob_dir.glob(".tmp-*"))

    def test_per_blob_and_total_limits_are_enforced(self, tmp_path: Path) -> None:
        small = tmp_path / "small.bin"
        small.write_bytes(b"x" * 100)
        knowledge = tmp_path / "knowledge.bin"
        knowledge.write_bytes(b"y" * 100)
        root = tmp_path / "layout"
        root.mkdir()
        with pytest.raises(OciValidationError, match="per-blob limit"):
            assemble_oci_layout(make_config(), {"behavior": small}, root, max_blob_bytes=50)
        assert not list((root / "blobs" / "sha256").glob(".tmp-*"))

        total_root = tmp_path / "layout-total"
        total_root.mkdir()
        with pytest.raises(OciValidationError, match="Total blob size"):
            assemble_oci_layout(
                make_config(),
                {"behavior": small, "knowledge": knowledge},
                total_root,
                max_total_blob_bytes=150,
            )
        assert not list((total_root / "blobs" / "sha256").glob(".tmp-*"))

    def test_symlink_blob_source_is_rejected(self, tmp_path: Path) -> None:
        target = tmp_path / "target.bin"
        target.write_bytes(b"x")
        link = tmp_path / "link.bin"
        link.symlink_to(target)
        root = tmp_path / "layout"
        root.mkdir()
        with pytest.raises(OciValidationError, match="symlink"):
            assemble_oci_layout(make_config(), {"behavior": link}, root)

    def test_reassembly_is_idempotent_and_safe(self, tmp_path: Path) -> None:
        source = tmp_path / "same.bin"
        source.write_bytes(b"same bytes")
        root = tmp_path / "layout"
        root.mkdir()
        first = assemble_oci_layout(make_config(), {"behavior": source}, root)
        second = assemble_oci_layout(make_config(), {"behavior": source}, root)
        assert first.blob_digests == second.blob_digests
        assert first.manifest_digest == second.manifest_digest
        blob_dir = root / "blobs" / "sha256"
        assert not list(blob_dir.glob(".tmp-*"))
        assert len(list(blob_dir.iterdir())) == len(first.blob_digests) + 1

    def test_existing_mismatched_blob_is_never_overwritten(self, tmp_path: Path) -> None:
        source = tmp_path / "x.bin"
        source.write_bytes(b"x")
        root = tmp_path / "layout"
        blob_dir = root / "blobs" / "sha256"
        blob_dir.mkdir(parents=True)
        expected = digest_bytes(b"x").removeprefix("sha256:")
        final = blob_dir / expected
        final.write_bytes(b"wrong content")
        with pytest.raises(OciValidationError, match="mismatched existing blob"):
            assemble_oci_layout(make_config(), {"behavior": source}, root)
        assert final.read_bytes() == b"wrong content"
        assert not list(blob_dir.glob(".tmp-*"))

    def test_shared_deadline_is_cumulative_across_blobs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first = tmp_path / "first.bin"
        first.write_bytes(b"a" * 100)
        second = tmp_path / "second.bin"
        second.write_bytes(b"b" * 100)
        root = tmp_path / "layout"
        root.mkdir()
        calls = {"count": 0}

        def clock() -> float:
            calls["count"] += 1
            return 0.0 if calls["count"] < 6 else 1.0

        monkeypatch.setattr("zana_core.images.oci.time.monotonic", clock)
        with pytest.raises(OciValidationError, match="deadline"):
            assemble_oci_layout(
                make_config(),
                {"behavior": first, "knowledge": second},
                root,
                deadline_seconds=0.5,
            )
        blob_dir = root / "blobs" / "sha256"
        assert not list(blob_dir.glob(".tmp-*"))

    def test_existing_blob_verification_respects_deadline(self, tmp_path: Path) -> None:
        blob_dir = tmp_path / "blobs" / "sha256"
        blob_dir.mkdir(parents=True)
        digest = digest_bytes(b"existing")
        final = blob_dir / digest.removeprefix("sha256:")
        final.write_bytes(b"existing")
        with pytest.raises(OciValidationError, match="deadline"):
            _verify_existing_blob(
                final,
                digest,
                chunk_size=64,
                max_blob_bytes=4096,
                start=0.0,
                deadline_seconds=-1.0,
            )

    def test_existing_blob_verification_respects_bounds(self, tmp_path: Path) -> None:
        blob_dir = tmp_path / "blobs" / "sha256"
        blob_dir.mkdir(parents=True)
        digest = digest_bytes(b"existing")
        final = blob_dir / digest.removeprefix("sha256:")
        final.write_bytes(b"existing longer content")
        with pytest.raises(OciValidationError, match="per-blob limit"):
            _verify_existing_blob(
                final,
                digest,
                chunk_size=64,
                max_blob_bytes=8,
                start=time.monotonic(),
                deadline_seconds=30.0,
            )


class TestValidation:
    def test_valid_layout_validates_with_digest_verification(
        self,
        assembled: tuple[ZanaImageConfig, Path, Path],
    ) -> None:
        _, root, _ = assembled
        validated = validate_oci_layout(root)
        assert validated.config.name == "policy-assistant"
        assert validated.config_digest.startswith("sha256:")
        assert len(validated.blob_digests) == 1

    def test_mutated_blob_is_rejected(
        self,
        assembled: tuple[ZanaImageConfig, Path, Path],
    ) -> None:
        config, root, _ = assembled
        behavior_digest = digest_bytes(b'{"policy": "helpful"}')
        blob_path = root / "blobs" / "sha256" / behavior_digest.removeprefix("sha256:")
        blob_path.write_bytes(b'{"policy": "tampered"}')
        with pytest.raises(OciValidationError, match="digest mismatch"):
            validate_oci_layout(root)

    def test_mutated_config_is_rejected(
        self,
        assembled: tuple[ZanaImageConfig, Path, Path],
    ) -> None:
        _, root, _ = assembled
        manifest = json.loads((root / "manifest.json").read_text())
        config_digest = manifest["config"]["digest"]
        config_path = root / "blobs" / "sha256" / config_digest.removeprefix("sha256:")
        config_payload = json.loads(config_path.read_text())
        config_payload["name"] = "renamed"
        config_path.write_text(json.dumps(config_payload, sort_keys=True))
        with pytest.raises(OciValidationError, match="digest mismatch"):
            validate_oci_layout(root)

    def test_malformed_index_is_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "layout"
        root.mkdir()
        (root / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}')
        (root / "index.json").write_text("{not json")
        with pytest.raises(OciValidationError, match="Malformed"):
            validate_oci_layout(root)

    def test_unsupported_layout_version_is_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "layout"
        root.mkdir()
        (root / "oci-layout").write_text('{"imageLayoutVersion":"9.9"}')
        (root / "index.json").write_text("{}")
        with pytest.raises(OciValidationError, match="Unsupported OCI layout"):
            validate_oci_layout(root)

    def test_unsupported_config_media_type_is_rejected(
        self,
        assembled: tuple[ZanaImageConfig, Path, Path],
    ) -> None:
        _, root, _ = assembled
        manifest = json.loads((root / "manifest.json").read_text())
        manifest["config"]["mediaType"] = "application/vnd.example"
        (root / "manifest.json").write_text(json.dumps(manifest, sort_keys=True))
        manifest_digest = digest_bytes((root / "manifest.json").read_bytes())
        index = json.loads((root / "index.json").read_text())
        index["manifests"][0]["digest"] = manifest_digest
        index["manifests"][0]["size"] = len((root / "manifest.json").read_bytes())
        (root / "index.json").write_text(json.dumps(index, sort_keys=True))
        with pytest.raises(OciValidationError, match="Unsupported config media type"):
            validate_oci_layout(root)

    def test_manifest_digest_mismatch_is_rejected(
        self,
        assembled: tuple[ZanaImageConfig, Path, Path],
    ) -> None:
        _, root, _ = assembled
        (root / "manifest.json").write_text('{"schemaVersion":2}')
        with pytest.raises(OciValidationError):
            validate_oci_layout(root)

    def test_missing_layer_blob_is_rejected(self, tmp_path: Path) -> None:
        behavior = tmp_path / "behavior.json"
        behavior.write_bytes(b"content")
        root = tmp_path / "layout"
        root.mkdir()
        assemble_oci_layout(make_config(), {"behavior": behavior}, root)
        validated = validate_oci_layout(root)
        blob = root / "blobs" / "sha256" / validated.blob_digests[0].removeprefix("sha256:")
        blob.unlink()
        with pytest.raises(OciValidationError, match="layer blob is missing"):
            validate_oci_layout(root)

    def test_secret_value_in_config_is_rejected(
        self,
        assembled: tuple[ZanaImageConfig, Path, Path],
    ) -> None:
        _, root, _ = assembled
        _inject_secret_config_value(root)
        with pytest.raises(OciValidationError, match="secret"):
            validate_oci_layout(root)

    def test_json_size_limit_is_enforced(
        self,
        assembled: tuple[ZanaImageConfig, Path, Path],
    ) -> None:
        _, root, _ = assembled
        with pytest.raises(OciValidationError, match="size limit"):
            validate_oci_layout(root, max_json_bytes=1)

    def test_deadline_is_enforced(
        self,
        assembled: tuple[ZanaImageConfig, Path, Path],
    ) -> None:
        _, root, _ = assembled
        with pytest.raises(OciValidationError, match="deadline"):
            validate_oci_layout(root, deadline_seconds=-1.0)

    def test_descriptor_annotation_caps(self) -> None:
        with pytest.raises(ValidationError):
            Descriptor(
                media_type="application/json",
                digest=BASE_DIGEST,
                size=1,
                annotations={"k" * 600: "v"},
            )
        with pytest.raises(ValidationError):
            Descriptor(
                media_type="application/json",
                digest=BASE_DIGEST,
                size=1,
                annotations={f"key-{index}": "v" * 5000 for index in range(3)},
            )
        with pytest.raises(ValidationError):
            Descriptor(
                media_type="application/json",
                digest=BASE_DIGEST,
                size=1,
                annotations={f"key-{index}": "v" for index in range(70)},
            )

    def test_manifest_and_index_object_graph_caps(self) -> None:
        descriptor = Descriptor(
            media_type="application/vnd.zana.image.config.v1+json",
            digest=BASE_DIGEST,
            size=1,
        )
        with pytest.raises(ValidationError):
            Index(manifests=[descriptor] * 9)
        with pytest.raises(ValidationError):
            Manifest(config=descriptor, layers=[descriptor] * (MAX_LAYERS + 1))

    def test_default_json_cap_applies_to_validation(self, tmp_path: Path) -> None:
        root = tmp_path / "layout"
        root.mkdir()
        (root / "oci-layout").write_bytes(b"x" * (1024 * 1024 + 1))
        with pytest.raises(OciValidationError, match="size limit"):
            validate_oci_layout(root)

    def test_validation_blob_byte_cap(self, tmp_path: Path) -> None:
        behavior = tmp_path / "behavior.json"
        behavior.write_bytes(b"content")
        root = tmp_path / "layout"
        root.mkdir()
        assemble_oci_layout(make_config(), {"behavior": behavior}, root)
        with pytest.raises(OciValidationError, match="per-blob byte limit"):
            validate_oci_layout(root, max_blob_bytes=4)

    def test_oci_models_are_frozen_and_reject_extras(self) -> None:
        with pytest.raises(ValidationError):
            Descriptor(
                media_type="application/json",
                digest=BASE_DIGEST,
                size=1,
                unexpected_key="x",
            )
        descriptor = Descriptor(media_type="application/json", digest=BASE_DIGEST, size=1)
        with pytest.raises(ValidationError):
            descriptor.size = 2

    def test_assemble_rejects_hostile_mapping_before_key_iteration(self, tmp_path: Path) -> None:
        from collections.abc import Mapping

        class HostileMapping(Mapping):
            def __len__(self) -> int:
                return 100

            def __getitem__(self, key):
                raise AssertionError("keys must not be materialized")

            def __iter__(self):
                raise AssertionError("keys must not be materialized")

        with pytest.raises(OciValidationError, match="builtin mapping"):
            assemble_oci_layout(make_config(), HostileMapping(), tmp_path / "layout")

    def test_generic_errors_do_not_leak_paths_or_values(self, tmp_path: Path) -> None:
        layout = tmp_path / "layout"
        layout.mkdir()
        (layout / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}')
        (layout / "index.json").write_text("{broken")
        try:
            validate_oci_layout(layout)
        except OciValidationError as error:
            assert str(tmp_path) not in str(error)
            assert "broken" not in str(error)
        else:
            raise AssertionError("must fail")

    def test_assemble_rejects_hard_limit_violations(self, tmp_path: Path) -> None:
        source = tmp_path / "b.bin"
        source.write_bytes(b"x")
        for kwargs in (
            {"max_blob_bytes": None},
            {"chunk_size": 0},
            {"deadline_seconds": float("inf")},
            {"max_total_blob_bytes": -1},
        ):
            with pytest.raises(OciValidationError):
                assemble_oci_layout(make_config(), {"behavior": source}, tmp_path / "x", **kwargs)


def _inject_secret_config_value(root: Path) -> None:
    """Test-only helper: add a secret key to the config blob and fix digests."""
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    config_digest = manifest["config"]["digest"]
    config_path = root / "blobs" / "sha256" / config_digest.removeprefix("sha256:")
    config_payload = json.loads(config_path.read_text(encoding="utf-8"))
    config_payload["runtime"] = {"endpoint_token": "sk-live-value"}
    encoded = json.dumps(config_payload, sort_keys=True, separators=(",", ":")).encode()
    new_digest = digest_bytes(encoded)
    (root / "blobs" / "sha256" / new_digest.removeprefix("sha256:")).write_bytes(encoded)
    config_path.unlink()
    manifest["config"]["digest"] = new_digest
    manifest["config"]["size"] = len(encoded)
    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    (root / "manifest.json").write_bytes(manifest_bytes)
    new_manifest_digest = digest_bytes(manifest_bytes)
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    index["manifests"][0]["digest"] = new_manifest_digest
    index["manifests"][0]["size"] = len(manifest_bytes)
    (root / "index.json").write_bytes(
        json.dumps(index, sort_keys=True, separators=(",", ":")).encode()
    )


class _RecordingReader:
    def __init__(self, handle: object, reads: list[int], max_read: int) -> None:
        self._handle = handle
        self._reads = reads
        self._max_read = max_read

    def read(self, size: int = -1) -> bytes:
        if size < 0 or size > self._max_read:
            raise AssertionError(f"unbounded read requested: {size}")
        chunk = self._handle.read(size)
        self._reads.append(len(chunk))
        return chunk

    def __enter__(self) -> _RecordingReader:
        return self

    def __exit__(self, *args: object) -> None:
        self._handle.close()


class _TruncatingReader:
    def __init__(self, handle: object) -> None:
        self._handle = handle

    def read(self, size: int = -1) -> bytes:
        self._handle.read(size)
        return b""

    def __enter__(self) -> _TruncatingReader:
        return self

    def __exit__(self, *args: object) -> None:
        self._handle.close()


class _FailingReader:
    def __init__(self, handle: object) -> None:
        self._handle = handle
        self._emitted = False

    def read(self, size: int = -1) -> bytes:
        if not self._emitted:
            self._emitted = True
            return self._handle.read(size)
        raise OSError("simulated failure")

    def __enter__(self) -> _FailingReader:
        return self

    def __exit__(self, *args: object) -> None:
        self._handle.close()
