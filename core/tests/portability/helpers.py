"""Shared helpers building tiny real canonical OCI layouts and archives."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

from zana_core.images.models import BaseModelReference, ZanaImageConfig
from zana_core.images.oci import assemble_oci_layout


def default_config(*, base_model_digest: str | None = None) -> ZanaImageConfig:
    """Return a valid canonical image config with optional exact base digest."""
    return ZanaImageConfig(
        name="policy-assistant",
        version="1.0.0",
        base_model=BaseModelReference(
            display_name="example-model",
            identity_digest=base_model_digest,
            runtime_compatibility=["ollama"],
            required_capabilities=["completion"],
        ),
    )


def build_layout(
    root: Path,
    *,
    config: ZanaImageConfig | None = None,
    layer_bytes: bytes | None = None,
) -> tuple[Path, str]:
    """Write a valid tiny OCI layout via the canonical assembler."""
    active = config if config is not None else default_config()
    root.mkdir(parents=True, exist_ok=True)
    behavior = root / "behavior.json"
    behavior.write_bytes(layer_bytes if layer_bytes is not None else b'{"policy":"helpful"}')
    layout = root / "layout"
    layout.mkdir(parents=True, exist_ok=True)
    result = assemble_oci_layout(active, {"behavior": behavior}, layout)
    return layout, result.image_digest


def corrupt_layout_config_with_secret(layout: Path) -> None:
    """Rewrite the config blob to carry a secret value and fix manifest/index.

    Test-only helper; production never hand-assembles digests.
    """
    manifest = json.loads((layout / "manifest.json").read_text(encoding="utf-8"))
    config_digest = manifest["config"]["digest"]
    config_path = layout / "blobs" / "sha256" / config_digest.removeprefix("sha256:")
    config_payload = json.loads(config_path.read_text(encoding="utf-8"))
    config_payload["runtime"] = {"endpoint_token": "sk-live-value"}
    encoded = json.dumps(config_payload, sort_keys=True, separators=(",", ":")).encode()
    from zana_core.artifacts import digest_bytes

    new_digest = digest_bytes(encoded)
    (layout / "blobs" / "sha256" / new_digest.removeprefix("sha256:")).write_bytes(encoded)
    config_path.unlink()
    manifest["config"]["digest"] = new_digest
    manifest["config"]["size"] = len(encoded)
    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    (layout / "manifest.json").write_bytes(manifest_bytes)
    new_manifest_digest = digest_bytes(manifest_bytes)
    index = json.loads((layout / "index.json").read_text(encoding="utf-8"))
    index["manifests"][0]["digest"] = new_manifest_digest
    index["manifests"][0]["size"] = len(manifest_bytes)
    (layout / "index.json").write_bytes(
        json.dumps(index, sort_keys=True, separators=(",", ":")).encode()
    )


def archive_file(path: Path, layout: Path) -> Path:
    """Write a deterministic canonical tar archive from a layout."""
    from zana_core.images.archive import TarCodec

    TarCodec().pack(layout, path)
    return path


def tar_with_members(members: list[tuple[str, bytes]]) -> bytes:
    """Build a tar archive from (name, content) pairs for unsafe tests."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, content in members:
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()
