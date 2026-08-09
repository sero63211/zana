"""Adapter validation with read-only SHA-256 verification and provenance."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from zana_core.training.contracts import AdapterBaseIdentity, AdapterMetadata


class ArtifactVerifier(Protocol):
    """Read-only verifier injected so tests never need fake adapter bytes."""

    def sha256(self, path: Path) -> str: ...


def sha256_file(path: Path) -> str:
    """Read a file in chunks and return its SHA-256 hex digest."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


class LocalArtifactVerifier:
    """Default read-only artifact verifier."""

    def sha256(self, path: Path) -> str:
        return sha256_file(path)


@dataclass(frozen=True, slots=True)
class AdapterValidation:
    """Outcome of adapter artifact validation."""

    ok: bool
    reason: str
    digest: str | None = None


def validate_adapter(
    *,
    path: Path,
    base: AdapterBaseIdentity,
    provider: str,
    dataset_digest: str,
    config_digest: str,
    provider_version: str,
    verifier: ArtifactVerifier | None = None,
) -> tuple[AdapterValidation, AdapterMetadata | None]:
    """Validate safetensors expectation, digest, and exact base binding."""
    if path.suffix != ".safetensors":
        return AdapterValidation(False, "adapter must be a .safetensors file"), None
    try:
        digest = (verifier or LocalArtifactVerifier()).sha256(path)
    except OSError as error:
        return AdapterValidation(False, f"adapter could not be read: {error}"), None
    if base.base_model_digest != base.training_source_digest:
        return AdapterValidation(False, "adapter base digest is not proven"), None
    if provider not in ("mlx_lm", "hf_peft"):
        return AdapterValidation(False, f"unsupported provider {provider!r}"), None
    if not dataset_digest or not config_digest or not provider_version:
        return AdapterValidation(False, "dataset/config/provider provenance is incomplete"), None
    metadata = AdapterMetadata(
        type="lora",
        format="safetensors",
        base_model_digest=base.base_model_digest,
        training_provider=provider,
        training_provider_version=provider_version,
        dataset_digest=dataset_digest,
        config_digest=config_digest,
        adapter_digest=digest,
        seed=0,
    )
    return AdapterValidation(True, "adapter validated", digest), metadata
