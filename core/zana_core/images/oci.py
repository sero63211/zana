"""Deterministic OCI Image Layout assembly and validation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from zana_core.artifacts.digest import (
    InvalidDigestError,
    digest_bytes,
    digest_stream,
    validate_digest,
)
from zana_core.images.models import ZanaImageConfig, validate_config_digests

OCI_LAYOUT_VERSION = "1.0.0"
MEDIA_TYPE_OCI_LAYOUT = "application/vnd.oci.image.layout.v1+json"
MEDIA_TYPE_OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
MEDIA_TYPE_OCI_INDEX = "application/vnd.oci.image.index.v1+json"
MEDIA_TYPE_ZANA_CONFIG = "application/vnd.zana.image.config.v1+json"
MEDIA_TYPE_ZANA_BEHAVIOR = "application/vnd.zana.behavior.v1+json"
MEDIA_TYPE_ZANA_KNOWLEDGE = "application/vnd.zana.knowledge.v1.tar+zstd"
MEDIA_TYPE_ZANA_ADAPTER = "application/vnd.zana.adapter.v1.tar+zstd"
MEDIA_TYPE_ZANA_TOOLS = "application/vnd.zana.tools.v1+json"
MEDIA_TYPE_ZANA_PERMISSIONS = "application/vnd.zana.permissions.v1+json"
MEDIA_TYPE_ZANA_EVALUATION = "application/vnd.zana.evaluation.v1+json"
MEDIA_TYPE_ZANA_REPORT = "application/vnd.zana.report.v1+json"

ROLE_MEDIA_TYPES: dict[str, str] = {
    "behavior": MEDIA_TYPE_ZANA_BEHAVIOR,
    "knowledge": MEDIA_TYPE_ZANA_KNOWLEDGE,
    "adapter": MEDIA_TYPE_ZANA_ADAPTER,
    "tools": MEDIA_TYPE_ZANA_TOOLS,
    "permissions": MEDIA_TYPE_ZANA_PERMISSIONS,
    "evaluation": MEDIA_TYPE_ZANA_EVALUATION,
    "report": MEDIA_TYPE_ZANA_REPORT,
}


class OciValidationError(ValueError):
    """Raised when an OCI layout is malformed, unsafe, or corrupted."""


class Descriptor(BaseModel):
    """OCI content descriptor."""

    model_config = ConfigDict(
        populate_by_name=True,
        extra="allow",
        alias_generator=(
            lambda field_name: "mediaType" if field_name == "media_type" else field_name
        ),
    )

    media_type: str
    digest: str
    size: int = Field(ge=0)
    annotations: dict[str, str] = Field(default_factory=dict)

    @field_validator("media_type")
    @classmethod
    def validate_media_type(cls, value: str) -> str:
        if not value.startswith("application/"):
            raise ValueError("OCI descriptor media type must use application/.")
        return value

    @field_validator("digest")
    @classmethod
    def validate_digest_field(cls, value: str) -> str:
        return validate_digest(value)


class Manifest(BaseModel):
    """OCI image manifest."""

    model_config = ConfigDict(populate_by_name=True)

    schema_version: int = 2
    media_type: str = MEDIA_TYPE_OCI_MANIFEST
    config: Descriptor
    layers: list[Descriptor] = Field(default_factory=list)


class Index(BaseModel):
    """OCI image index."""

    model_config = ConfigDict(populate_by_name=True)

    schema_version: int = 2
    media_type: str = MEDIA_TYPE_OCI_INDEX
    manifests: list[Descriptor] = Field(default_factory=list)


class OciLayoutFile(BaseModel):
    """Content of the OCI ``oci-layout`` file."""

    model_config = ConfigDict(alias_generator=None, populate_by_name=True)

    image_layout_version: str = Field(
        default=OCI_LAYOUT_VERSION,
        validation_alias=AliasChoices("imageLayoutVersion", "image_layout_version"),
        serialization_alias="imageLayoutVersion",
    )


@dataclass(frozen=True)
class OciLayoutResult:
    """Deterministic OCI layout output and canonical digests."""

    root: Path
    config_digest: str
    manifest_digest: str
    index_digest: str
    image_digest: str
    blob_digests: tuple[str, ...]


@dataclass(frozen=True)
class ValidatedLayout:
    """Parsed, digest-verified OCI layout contents."""

    root: Path
    config: ZanaImageConfig
    config_digest: str
    manifest_digest: str
    index_digest: str
    blob_digests: tuple[str, ...]
    total_size: int


def canonical_json_bytes(data: dict[str, Any] | BaseModel) -> bytes:
    """Serialize an object deterministically (sorted keys, compact JSON)."""
    if isinstance(data, BaseModel):
        payload = data.model_dump(mode="json", exclude_none=True)
    else:
        payload = data
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _blob_digest(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            return digest_stream(handle)
    except OSError as error:
        raise OciValidationError(f"Could not read blob {path}: {error}") from error


def assemble_oci_layout(
    config: ZanaImageConfig,
    blobs: Mapping[str, Path],
    root: Path,
) -> OciLayoutResult:
    """Build a deterministic OCI layout rooted at ``root``.

    ``blobs`` maps immutable role names to regular files. Roles outside the
    canonical set and secret/mutable-state members are rejected by the caller
    before assembly through the export safety scanner.
    """

    validate_config_digests(config)
    unknown_roles = set(blobs).difference(ROLE_MEDIA_TYPES)
    if unknown_roles:
        raise OciValidationError(f"Unknown image blob roles: {sorted(unknown_roles)}")

    blob_root = root / "blobs" / "sha256"
    blob_root.mkdir(parents=True, exist_ok=True)
    config_bytes = canonical_json_bytes(config)
    config_digest = digest_bytes(config_bytes)

    layer_descriptors: list[Descriptor] = []
    blob_digests: list[str] = []
    for role in sorted(blobs):
        source = blobs[role]
        if not source.is_file():
            raise OciValidationError(f"Blob source is not a regular file: {source}")
        digest = _blob_digest(source)
        size = source.stat().st_size
        blob_digests.append(digest)
        (blob_root / digest.removeprefix("sha256:")).write_bytes(source.read_bytes())
        layer_descriptors.append(
            Descriptor(
                media_type=ROLE_MEDIA_TYPES[role],
                digest=digest,
                size=size,
                annotations={"org.zana.role": role},
            )
        )

    (blob_root / config_digest.removeprefix("sha256:")).write_bytes(config_bytes)
    manifest = Manifest(
        config=Descriptor(
            media_type=MEDIA_TYPE_ZANA_CONFIG,
            digest=config_digest,
            size=len(config_bytes),
        ),
        layers=layer_descriptors,
    )
    manifest_bytes = canonical_json_bytes(manifest)
    manifest_digest = digest_bytes(manifest_bytes)
    (root / "index.json").unlink(missing_ok=True)

    index = Index(
        manifests=[
            Descriptor(
                media_type=MEDIA_TYPE_OCI_MANIFEST,
                digest=manifest_digest,
                size=len(manifest_bytes),
            )
        ]
    )
    index_bytes = canonical_json_bytes(index)
    index_digest = digest_bytes(index_bytes)
    (root / "index.json").write_bytes(index_bytes)
    (root / "oci-layout").write_bytes(canonical_json_bytes(OciLayoutFile()))
    (root / "manifest.json").write_bytes(manifest_bytes)

    return OciLayoutResult(
        root=root,
        config_digest=config_digest,
        manifest_digest=manifest_digest,
        index_digest=index_digest,
        image_digest=index_digest,
        blob_digests=tuple(blob_digests),
    )


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OciValidationError(f"Invalid {label}: {error}") from error


def _require_digest(value: Any, label: str) -> str:
    try:
        return validate_digest(value)
    except InvalidDigestError as error:
        raise OciValidationError(f"{label} has invalid digest {value!r}.") from error


def _verify_file(digest: str, path: Path, label: str) -> int:
    if not path.is_file():
        raise OciValidationError(f"{label} blob is missing: {path}")
    try:
        with path.open("rb") as handle:
            actual = digest_stream(handle)
            size = handle.seek(0, 2)
    except OSError as error:
        raise OciValidationError(f"{label} blob could not be read: {error}") from error
    if actual != digest:
        raise OciValidationError(f"{label} digest mismatch: expected {digest}, found {actual}.")
    return size


def validate_oci_layout(root: Path) -> ValidatedLayout:
    """Validate layout version, JSON shape, descriptors, and every blob digest."""

    root = Path(root)
    layout = _read_json(root / "oci-layout", "oci-layout")
    if (
        layout.get("imageLayoutVersion") != OCI_LAYOUT_VERSION
        and layout.get("image_layout_version") != OCI_LAYOUT_VERSION
    ):
        raise OciValidationError(
            f"Unsupported OCI layout version: {layout.get('imageLayoutVersion')!r}"
        )

    index_payload = _read_json(root / "index.json", "index.json")
    try:
        index = Index.model_validate(index_payload)
    except Exception as error:
        raise OciValidationError(f"Malformed OCI index: {error}") from error
    if index.schema_version != 2 or index.media_type != MEDIA_TYPE_OCI_INDEX:
        raise OciValidationError("Unsupported OCI index schema or media type.")
    if len(index.manifests) != 1:
        raise OciValidationError("ZANA imports require exactly one image manifest.")

    manifest_descriptor = index.manifests[0]
    if manifest_descriptor.media_type != MEDIA_TYPE_OCI_MANIFEST:
        raise OciValidationError("Index descriptor is not an OCI image manifest.")
    manifest_digest = _require_digest(manifest_descriptor.digest, "manifest")
    manifest_path = root / "manifest.json"
    manifest_size = _verify_file(manifest_digest, manifest_path, "manifest")
    if manifest_size != manifest_descriptor.size:
        raise OciValidationError("Manifest descriptor size does not match the file.")

    manifest_payload = _read_json(manifest_path, "manifest.json")
    try:
        manifest = Manifest.model_validate(manifest_payload)
    except Exception as error:
        raise OciValidationError(f"Malformed OCI manifest: {error}") from error
    if manifest.schema_version != 2 or manifest.media_type != MEDIA_TYPE_OCI_MANIFEST:
        raise OciValidationError("Unsupported OCI manifest schema or media type.")
    index_digest = digest_bytes((root / "index.json").read_bytes())

    config_descriptor = manifest.config
    if config_descriptor.media_type != MEDIA_TYPE_ZANA_CONFIG:
        raise OciValidationError(f"Unsupported config media type: {config_descriptor.media_type}")
    config_digest = _require_digest(config_descriptor.digest, "config")
    config_path = root / "blobs" / "sha256" / config_digest.removeprefix("sha256:")
    config_size = _verify_file(config_digest, config_path, "config")
    if config_size != config_descriptor.size:
        raise OciValidationError("Config descriptor size does not match the file.")

    config_payload = _read_json(config_path, "config")
    try:
        config = ZanaImageConfig.model_validate(config_payload)
    except Exception as error:
        raise OciValidationError(f"Unsupported or malformed ZANA image config: {error}") from error
    validate_config_digests(config)

    blob_digests: list[str] = []
    total_size = config_size
    for descriptor in manifest.layers:
        if descriptor.media_type not in set(ROLE_MEDIA_TYPES.values()):
            raise OciValidationError(f"Unsupported blob media type: {descriptor.media_type}")
        digest = _require_digest(descriptor.digest, "layer")
        path = root / "blobs" / "sha256" / digest.removeprefix("sha256:")
        size = _verify_file(digest, path, "layer")
        if size != descriptor.size:
            raise OciValidationError("Layer descriptor size does not match the file.")
        blob_digests.append(digest)
        total_size += size

    return ValidatedLayout(
        root=root,
        config=config,
        config_digest=config_digest,
        manifest_digest=manifest_digest,
        index_digest=index_digest,
        blob_digests=tuple(blob_digests),
        total_size=total_size,
    )
