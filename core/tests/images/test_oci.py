"""Deterministic OCI layout assembly, corruption, and validation tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zana_core.artifacts.digest import digest_bytes
from zana_core.images.models import BaseModelReference, ZanaImageConfig
from zana_core.images.oci import (
    OciValidationError,
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
        with pytest.raises(OciValidationError, match="Invalid index"):
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
