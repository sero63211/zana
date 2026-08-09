"""Deterministic OCI Image Layout assembly and validation."""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import time
import uuid
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from zana_core.artifacts.digest import (
    DEFAULT_CHUNK_SIZE,
    InvalidDigestError,
    digest_bytes,
    validate_digest,
)
from zana_core.images.archive import MAX_MEMBER_BYTES, MAX_TOTAL_BYTES
from zana_core.images.models import (
    Adapter,
    BaseModelReference,
    Behavior,
    BuildMetadata,
    Chunker,
    Evaluation,
    KnowledgeSnapshot,
    Permissions,
    Tool,
    ZanaImageConfig,
    validate_config_digests,
)
from zana_core.images.secrets import scan_payload_for_secrets

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

MAX_INDEX_MANIFESTS = 8
MAX_LAYERS = 128
MAX_ANNOTATIONS = 64
MAX_ANNOTATION_KEY_CHARS = 512
MAX_ANNOTATION_VALUE_CHARS = 4096
MAX_JSON_BYTES_DEFAULT = 1024 * 1024
MAX_VALIDATION_BLOB_BYTES = 16 * 1024**3
MAX_VALIDATION_TOTAL_BYTES = 32 * 1024**3
MAX_MEDIA_TYPE_CHARS = 300
MAX_DESCRIPTOR_SIZE = 16 * 1024**3
MAX_OCI_DEADLINE_SECONDS = 3600.0
MAX_OCI_CHUNK_BYTES = 1024 * 1024
MAX_CANONICAL_JSON_BYTES = MAX_JSON_BYTES_DEFAULT
MAX_CANONICAL_JSON_NODES = 100_000
_CONCRETE_PATH = type(Path())


def _require_os_support() -> None:
    """Fail closed unless all required path-open primitives exist."""
    for attribute in ("O_NOFOLLOW", "O_CLOEXEC", "O_DIRECTORY"):
        if not hasattr(os, attribute):
            raise OciValidationError("secure filesystem open is unsupported on this platform")


def _exact_oci_path(value: object) -> Path:
    if type(value) is not _CONCRETE_PATH:
        raise OciValidationError("path must be an exact concrete pathlib.Path")
    return value


def _oci_dir_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _oci_read_flags() -> int:
    return os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC


def _oci_exclusive_flags() -> int:
    return os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC


def _open_oci_parent_dirfd(path: Path) -> tuple[int, str]:
    """Open an exact absolute path's parent via all-component dirfd walk."""
    _require_os_support()
    _exact_oci_path(path)
    if not path.is_absolute():
        raise OciValidationError("path is not absolute")
    for part in path.parts:
        if part in ("", ".", ".."):
            raise OciValidationError("path contains an unsafe component")
    parent = path.parent
    fd = os.open(parent.anchor, _oci_dir_flags())
    try:
        for part in parent.parts[1:]:
            try:
                child_fd = os.open(part, _oci_dir_flags(), dir_fd=fd)
            except OSError as error:
                os.close(fd)
                raise OciValidationError("path ancestor could not be opened safely") from error
            os.close(fd)
            fd = child_fd
    except OSError as error:
        os.close(fd)
        raise OciValidationError("path ancestor could not be opened safely") from error
    return fd, path.name


def _open_oci_dirfd(path: Path) -> int:
    """Open an exact absolute directory path via all-component dirfd walk."""
    _require_os_support()
    _exact_oci_path(path)
    if not path.is_absolute():
        raise OciValidationError("path is not absolute")
    for part in path.parts:
        if part in ("", ".", ".."):
            raise OciValidationError("path contains an unsafe component")
    fd = os.open(path.anchor, _oci_dir_flags())
    try:
        for part in path.parts[1:]:
            try:
                child_fd = os.open(part, _oci_dir_flags(), dir_fd=fd)
            except OSError as error:
                os.close(fd)
                raise OciValidationError("path could not be opened safely") from error
            os.close(fd)
            fd = child_fd
    except OSError as error:
        os.close(fd)
        raise OciValidationError("path could not be opened safely") from error
    return fd


def _read_flags() -> int:
    _require_os_support()
    return _oci_read_flags()


def _dir_flags() -> int:
    _require_os_support()
    return _oci_dir_flags()


def _exclusive_write_flags() -> int:
    _require_os_support()
    return _oci_exclusive_flags()


@dataclass(frozen=True)
class ImmutableAnnotations:
    """Structurally immutable annotation map validated from exact dicts only."""

    items: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        self._validated_items()

    def _validated_items(self) -> tuple[tuple[str, str], ...]:
        items = self.items
        if type(items) is not tuple:
            raise OciValidationError("OCI annotations are malformed")
        if len(items) > MAX_ANNOTATIONS:
            raise OciValidationError("OCI annotation count exceeds the limit")
        collected: list[tuple[str, str]] = []
        for item in items:
            if type(item) is not tuple or len(item) != 2:
                raise OciValidationError("OCI annotation items are malformed")
            key, value = item
            if type(key) is not str or type(value) is not str:
                raise OciValidationError("OCI annotation keys and values must be strings")
            if len(key) > MAX_ANNOTATION_KEY_CHARS:
                raise OciValidationError("OCI annotation key exceeds the length limit")
            if len(value) > MAX_ANNOTATION_VALUE_CHARS:
                raise OciValidationError("OCI annotation value exceeds the length limit")
            collected.append((key, value))
        return tuple(collected)

    @classmethod
    def from_exact_dict(cls, value: object) -> ImmutableAnnotations:
        if type(value) is not dict:
            raise ValueError("OCI annotations must be an exact builtin mapping")
        if len(value) > MAX_ANNOTATIONS:
            raise ValueError("OCI annotation count exceeds the limit")
        collected: list[tuple[str, str]] = []
        for raw_key, raw_value in value.items():
            if type(raw_key) is not str or type(raw_value) is not str:
                raise ValueError("OCI annotation keys and values must be strings")
            if len(raw_key) > MAX_ANNOTATION_KEY_CHARS:
                raise ValueError("OCI annotation key exceeds the length limit")
            if len(raw_value) > MAX_ANNOTATION_VALUE_CHARS:
                raise ValueError("OCI annotation value exceeds the length limit")
            collected.append((raw_key, raw_value))
        return cls(tuple(collected))

    def as_dict(self) -> dict[str, str]:
        if type(self) is not ImmutableAnnotations:
            raise OciValidationError("OCI annotations must be the exact immutable type")
        return dict(self._validated_items())

    def get(self, key: str) -> str | None:
        for existing_key, value in self._validated_items():
            if existing_key == key:
                return value
        return None

    def __len__(self) -> int:
        return len(self._validated_items())

    def __iter__(self):
        return iter(self.as_dict())

    def __contains__(self, key: object) -> bool:
        if type(key) is not str:
            return False
        return any(existing_key == key for existing_key, _value in self._validated_items())


class OciValidationError(ValueError):
    """Raised when an OCI layout is malformed, unsafe, or corrupted."""


class Descriptor(BaseModel):
    """OCI content descriptor."""

    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
        frozen=True,
        strict=True,
        alias_generator=(
            lambda field_name: "mediaType" if field_name == "media_type" else field_name
        ),
    )

    media_type: str = Field(max_length=MAX_MEDIA_TYPE_CHARS)
    digest: str
    size: int = Field(strict=True, ge=0, le=MAX_DESCRIPTOR_SIZE)
    annotations: ImmutableAnnotations = Field(default_factory=lambda: ImmutableAnnotations(()))

    @field_validator("annotations", mode="before")
    @classmethod
    def require_exact_annotations(cls, value: object) -> ImmutableAnnotations:
        if type(value) is ImmutableAnnotations:
            value._validated_items()
            return value
        annotations = ImmutableAnnotations.from_exact_dict(value)
        annotations._validated_items()
        return annotations

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

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True, strict=True)

    schema_version: int = 2
    media_type: str = MEDIA_TYPE_OCI_MANIFEST
    config: Descriptor
    layers: tuple[Descriptor, ...] = Field(default_factory=tuple, max_length=MAX_LAYERS)

    @field_validator("layers", mode="before")
    @classmethod
    def require_exact_layers(cls, value: object) -> tuple[object, ...]:
        if type(value) not in (list, tuple):
            raise ValueError("manifest layers must be a builtin list or tuple")
        return tuple(value)  # type: ignore[arg-type]


class Index(BaseModel):
    """OCI image index."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True, strict=True)

    schema_version: int = 2
    media_type: str = MEDIA_TYPE_OCI_INDEX
    manifests: tuple[Descriptor, ...] = Field(default_factory=tuple, max_length=MAX_INDEX_MANIFESTS)

    @field_validator("manifests", mode="before")
    @classmethod
    def require_exact_manifests(cls, value: object) -> tuple[object, ...]:
        if type(value) not in (list, tuple):
            raise ValueError("index manifests must be a builtin list or tuple")
        return tuple(value)  # type: ignore[arg-type]


class OciLayoutFile(BaseModel):
    """Content of the OCI ``oci-layout`` file."""

    model_config = ConfigDict(
        alias_generator=None, populate_by_name=True, extra="forbid", frozen=True, strict=True
    )

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
    """Serialize an exact trusted model or prevalidated builtin JSON graph.

    Hostile hooks are never invoked: only exact builtin JSON scalars are
    accepted, NaN/Infinity is rejected, and the encoded output is capped.
    """
    trusted_models = (
        OciLayoutFile,
        Manifest,
        Index,
        Descriptor,
        ZanaImageConfig,
    )
    if type(data) in trusted_models:
        payload = _trusted_model_payload(data)  # type: ignore[arg-type]
    elif type(data) is dict:
        payload = data
    else:
        raise OciValidationError("Canonical JSON requires an exact trusted model or mapping")
    _validate_json_graph(payload)
    try:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise OciValidationError("Canonical JSON contains a non-serializable value") from error
    if len(encoded) > MAX_CANONICAL_JSON_BYTES:
        raise OciValidationError("Canonical JSON exceeds the byte limit")
    return encoded


def _trusted_model_payload(data: BaseModel) -> dict[str, Any]:
    if type(data) is OciLayoutFile:
        return _oci_layout_payload(data)
    if type(data) is Descriptor:
        return _descriptor_payload(data)
    if type(data) is Manifest:
        return _manifest_payload(data)
    if type(data) is Index:
        return _index_payload(data)
    if type(data) is ZanaImageConfig:
        return _config_payload(data)
    raise OciValidationError("Canonical JSON requires an exact trusted model or mapping")


def _raw_model_field(data: BaseModel, name: str) -> Any:
    raw = data.__dict__
    if type(raw) is not dict or name not in raw:
        raise OciValidationError("Trusted model is missing a required raw field")
    return raw[name]


def _exact_opt_str(value: Any, name: str, maximum: int) -> str | None:
    if value is None:
        return None
    if type(value) is not str or len(value) > maximum:
        raise OciValidationError(f"Trusted model field {name} is invalid")
    return value


def _exact_opt_digest(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        raise OciValidationError(f"Trusted model field {name} is invalid")
    validate_digest(value)
    return value


def _oci_layout_payload(data: OciLayoutFile) -> dict[str, Any]:
    version = _raw_model_field(data, "image_layout_version")
    if type(version) is not str or len(version) > 100:
        raise OciValidationError("Trusted OCI layout version is invalid")
    return {"imageLayoutVersion": version}


def _descriptor_payload(data: Descriptor) -> dict[str, Any]:
    media_type = _raw_model_field(data, "media_type")
    digest = _raw_model_field(data, "digest")
    size = _raw_model_field(data, "size")
    annotations = _raw_model_field(data, "annotations")
    if type(media_type) is not str or len(media_type) > MAX_MEDIA_TYPE_CHARS:
        raise OciValidationError("Trusted descriptor media type is invalid")
    if not media_type.startswith("application/"):
        raise OciValidationError("Trusted descriptor media type is invalid")
    if type(digest) is not str:
        raise OciValidationError("Trusted descriptor digest is invalid")
    validate_digest(digest)
    if type(size) is not int or not 0 <= size <= MAX_DESCRIPTOR_SIZE:
        raise OciValidationError("Trusted descriptor size is invalid")
    if type(annotations) is not ImmutableAnnotations:
        raise OciValidationError("Trusted descriptor annotations are invalid")
    return {
        "mediaType": media_type,
        "digest": digest,
        "size": size,
        "annotations": annotations.as_dict(),
    }


def _descriptor_tuple(value: Any, name: str) -> tuple[Descriptor, ...]:
    if type(value) is not tuple or len(value) > MAX_LAYERS:
        raise OciValidationError(f"Trusted model field {name} is invalid")
    for item in value:
        if type(item) is not Descriptor:
            raise OciValidationError(f"Trusted model field {name} is invalid")
    return value


def _manifest_payload(data: Manifest) -> dict[str, Any]:
    schema = _raw_model_field(data, "schema_version")
    media_type = _raw_model_field(data, "media_type")
    config = _raw_model_field(data, "config")
    layers = _raw_model_field(data, "layers")
    if type(schema) is not int or schema != 2:
        raise OciValidationError("Trusted manifest schema is invalid")
    if type(media_type) is not str or media_type != MEDIA_TYPE_OCI_MANIFEST:
        raise OciValidationError("Trusted manifest media type is invalid")
    if type(config) is not Descriptor:
        raise OciValidationError("Trusted manifest config is invalid")
    return {
        "schema_version": schema,
        "media_type": media_type,
        "config": _descriptor_payload(config),
        "layers": [_descriptor_payload(item) for item in _descriptor_tuple(layers, "layers")],
    }


def _index_payload(data: Index) -> dict[str, Any]:
    schema = _raw_model_field(data, "schema_version")
    media_type = _raw_model_field(data, "media_type")
    manifests = _raw_model_field(data, "manifests")
    if type(schema) is not int or schema != 2:
        raise OciValidationError("Trusted index schema is invalid")
    if type(media_type) is not str or media_type != MEDIA_TYPE_OCI_INDEX:
        raise OciValidationError("Trusted index media type is invalid")
    if type(manifests) is not tuple or len(manifests) > MAX_INDEX_MANIFESTS:
        raise OciValidationError("Trusted index manifests are invalid")
    for item in manifests:
        if type(item) is not Descriptor:
            raise OciValidationError("Trusted index manifests are invalid")
    return {
        "schema_version": schema,
        "media_type": media_type,
        "manifests": [_descriptor_payload(item) for item in manifests],
    }


def _config_payload(data: ZanaImageConfig) -> dict[str, Any]:
    schema = _raw_model_field(data, "schema_version")
    kind = _raw_model_field(data, "kind")
    name = _raw_model_field(data, "name")
    version = _raw_model_field(data, "version")
    base_model = _raw_model_field(data, "base_model")
    behavior = _raw_model_field(data, "behavior")
    knowledge = _raw_model_field(data, "knowledge")
    adapter = _raw_model_field(data, "adapter")
    tools = _raw_model_field(data, "tools")
    permissions = _raw_model_field(data, "permissions")
    evaluation = _raw_model_field(data, "evaluation")
    build = _raw_model_field(data, "build")
    if type(schema) is not int or schema != 1:
        raise OciValidationError("Trusted config schema is invalid")
    if type(kind) is not str or kind != "ZanaImage":
        raise OciValidationError("Trusted config kind is invalid")
    if type(name) is not str or not 1 <= len(name) <= 300:
        raise OciValidationError("Trusted config name is invalid")
    if type(version) is not str or not 1 <= len(version) <= 100:
        raise OciValidationError("Trusted config version is invalid")
    if type(base_model) is not BaseModelReference:
        raise OciValidationError("Trusted config base model is invalid")
    if type(tools) is not tuple or len(tools) > 128:
        raise OciValidationError("Trusted config tools are invalid")
    for tool in tools:
        if type(tool) is not Tool:
            raise OciValidationError("Trusted config tools are invalid")
    if type(permissions) is not Permissions:
        raise OciValidationError("Trusted config permissions are invalid")
    if type(evaluation) is not Evaluation:
        raise OciValidationError("Trusted config evaluation is invalid")
    if type(build) is not BuildMetadata:
        raise OciValidationError("Trusted config build metadata is invalid")
    payload: dict[str, Any] = {
        "schemaVersion": schema,
        "kind": kind,
        "name": name,
        "version": version,
        "base_model": _base_model_payload(base_model),
        "tools": [_tool_payload(tool) for tool in tools],
        "permissions": _permissions_payload(permissions),
        "evaluation": _evaluation_payload(evaluation),
        "build": _build_payload(build),
    }
    if behavior is not None:
        if type(behavior) is not Behavior:
            raise OciValidationError("Trusted config behavior is invalid")
        payload["behavior"] = _behavior_payload(behavior)
    if knowledge is not None:
        if type(knowledge) is not KnowledgeSnapshot:
            raise OciValidationError("Trusted config knowledge is invalid")
        payload["knowledge"] = _knowledge_payload(knowledge)
    if adapter is not None:
        if type(adapter) is not Adapter:
            raise OciValidationError("Trusted config adapter is invalid")
        payload["adapter"] = _adapter_payload(adapter)
    return payload


def _base_model_payload(value: BaseModelReference) -> dict[str, Any]:
    raw = value.__dict__
    if type(raw) is not dict:
        raise OciValidationError("Trusted base model is invalid")
    display = _exact_opt_str(raw.get("display_name"), "display_name", 500)
    family = _exact_opt_str(raw.get("family"), "family", 200)
    identity = _exact_opt_digest(raw.get("identity_digest"), "identity_digest")
    runtime = _exact_str_tuple(raw.get("runtime_compatibility"), "runtime_compatibility", 32, 200)
    required = _exact_str_tuple(raw.get("required_capabilities"), "required_capabilities", 32, 200)
    payload: dict[str, Any] = {
        "runtime_compatibility": list(runtime),
        "required_capabilities": list(required),
    }
    if display is not None:
        payload["display_name"] = display
    if family is not None:
        payload["family"] = family
    if identity is not None:
        payload["identity_digest"] = identity
    return payload


def _exact_str_tuple(value: Any, name: str, maximum: int, item_maximum: int) -> tuple[str, ...]:
    if type(value) is not tuple or len(value) > maximum:
        raise OciValidationError(f"Trusted model field {name} is invalid")
    for item in value:
        if type(item) is not str or len(item) > item_maximum:
            raise OciValidationError(f"Trusted model field {name} is invalid")
    return value


def _behavior_payload(value: Behavior) -> dict[str, Any]:
    raw = value.__dict__
    if type(raw) is not dict:
        raise OciValidationError("Trusted behavior is invalid")
    payload: dict[str, Any] = {}
    for field_name in ("system_policy_digest", "behavior_digest"):
        digest = _exact_opt_digest(raw.get(field_name), field_name)
        if digest is not None:
            payload[field_name] = digest
    return payload


def _knowledge_payload(value: KnowledgeSnapshot) -> dict[str, Any]:
    raw = value.__dict__
    if type(raw) is not dict:
        raise OciValidationError("Trusted knowledge snapshot is invalid")
    payload: dict[str, Any] = {}
    for field_name in ("snapshot_digest", "embedding_model_digest"):
        digest = _exact_opt_digest(raw.get(field_name), field_name)
        if digest is not None:
            payload[field_name] = digest
    identity = _exact_opt_str(raw.get("embedding_model_identity"), "embedding_model_identity", 500)
    if identity is not None:
        payload["embedding_model_identity"] = identity
    chunker = raw.get("chunker")
    if chunker is not None:
        from zana_core.images.models import Chunker

        if type(chunker) is not Chunker:
            raise OciValidationError("Trusted chunker is invalid")
        payload["chunker"] = _chunker_payload(chunker)
    return payload


def _chunker_payload(value: Chunker) -> dict[str, Any]:
    raw = value.__dict__
    if type(raw) is not dict:
        raise OciValidationError("Trusted chunker is invalid")
    chunker_id = _exact_opt_str(raw.get("id"), "id", 200)
    version = raw.get("version")
    if type(version) is not int or not 1 <= version <= 1000:
        raise OciValidationError("Trusted chunker version is invalid")
    payload: dict[str, Any] = {"id": chunker_id, "version": version}
    digest = _exact_opt_digest(raw.get("config_digest"), "config_digest")
    if digest is not None:
        payload["config_digest"] = digest
    return payload


def _adapter_payload(value: Adapter) -> dict[str, Any]:
    raw = value.__dict__
    if type(raw) is not dict:
        raise OciValidationError("Trusted adapter is invalid")
    adapter_type = _exact_opt_str(raw.get("type"), "type", 200)
    payload: dict[str, Any] = {"type": adapter_type}
    for field_name in (
        "digest",
        "base_model_digest",
        "training_config_digest",
        "dataset_digest",
    ):
        digest = _exact_opt_digest(raw.get(field_name), field_name)
        if digest is not None:
            payload[field_name] = digest
    provider = _exact_opt_str(raw.get("training_provider"), "training_provider", 200)
    if provider is not None:
        payload["training_provider"] = provider
    return payload


def _tool_payload(value: Tool) -> dict[str, Any]:
    raw = value.__dict__
    if type(raw) is not dict:
        raise OciValidationError("Trusted tool is invalid")
    tool_id = _exact_opt_str(raw.get("id"), "id", 200)
    version = raw.get("version")
    if type(version) is not int or not 1 <= version <= 1000:
        raise OciValidationError("Trusted tool version is invalid")
    payload: dict[str, Any] = {"id": tool_id, "version": version}
    digest = _exact_opt_digest(raw.get("digest"), "digest")
    if digest is not None:
        payload["digest"] = digest
    return payload


def _permissions_payload(value: Permissions) -> dict[str, Any]:
    raw = value.__dict__
    if type(raw) is not dict:
        raise OciValidationError("Trusted permissions are invalid")
    network = raw.get("network_outbound")
    if type(network) is not bool:
        raise OciValidationError("Trusted permissions network flag is invalid")
    payload: dict[str, Any] = {"network_outbound": network}
    digest = _exact_opt_digest(raw.get("digest"), "digest")
    if digest is not None:
        payload["digest"] = digest
    for field_name in ("filesystem_read", "filesystem_write", "tools_allow", "secrets_allow"):
        items = _exact_str_tuple(raw.get(field_name), field_name, 64, 500)
        payload[field_name] = list(items)
    return payload


def _evaluation_payload(value: Evaluation) -> dict[str, Any]:
    raw = value.__dict__
    if type(raw) is not dict:
        raise OciValidationError("Trusted evaluation is invalid")
    payload: dict[str, Any] = {}
    for field_name in ("suite_digest", "report_digest"):
        digest = _exact_opt_digest(raw.get(field_name), field_name)
        if digest is not None:
            payload[field_name] = digest
    status = _exact_opt_str(raw.get("status"), "status", 100)
    if status is not None:
        payload["status"] = status
    return payload


def _build_payload(value: BuildMetadata) -> dict[str, Any]:
    raw = value.__dict__
    if type(raw) is not dict:
        raise OciValidationError("Trusted build metadata is invalid")
    payload: dict[str, Any] = {}
    version = _exact_opt_str(raw.get("zana_version"), "zana_version", 100)
    if version is not None:
        payload["zana_version"] = version
    digest = _exact_opt_digest(raw.get("build_plan_digest"), "build_plan_digest")
    if digest is not None:
        payload["build_plan_digest"] = digest
    built_at = _exact_opt_str(raw.get("built_at"), "built_at", 100)
    if built_at is not None:
        payload["built_at"] = built_at
    return payload


def _validate_json_graph(value: Any, *, depth: int = 0) -> None:
    """Reject anything that is not an exact bounded builtin JSON-safe graph."""
    state = {"nodes": 0, "string_bytes": 0, "visited": set()}
    _validate_json_node(value, state, depth=0)


def _validate_json_node(value: Any, state: dict[str, Any], *, depth: int) -> None:
    if depth > 128:
        raise OciValidationError("Canonical JSON exceeds the depth limit")
    state["nodes"] += 1
    if state["nodes"] > MAX_CANONICAL_JSON_NODES:
        raise OciValidationError("Canonical JSON exceeds the node budget")
    if type(value) in (list, dict):
        identity = id(value)
        if identity in state["visited"]:
            raise OciValidationError("Canonical JSON contains a cyclic or aliased graph")
        state["visited"].add(identity)
    if type(value) in (type(None), bool, str):
        if type(value) is str:
            state["string_bytes"] += len(value.encode("utf-8", "surrogatepass"))
            if state["string_bytes"] > MAX_CANONICAL_JSON_BYTES:
                raise OciValidationError("Canonical JSON exceeds the string byte budget")
        return
    if type(value) is int:
        if not -(2**63) <= value <= 2**63 - 1:
            raise OciValidationError("Canonical JSON integer exceeds the signed 64-bit bound")
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise OciValidationError("Canonical JSON contains a non-finite number")
        return
    if type(value) is list:
        for item in value:
            _validate_json_node(item, state, depth=depth + 1)
        return
    if type(value) is tuple:
        for item in value:
            _validate_json_node(item, state, depth=depth + 1)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise OciValidationError("Canonical JSON keys must be strings")
            state["string_bytes"] += len(key.encode("utf-8", "surrogatepass"))
            if state["string_bytes"] > MAX_CANONICAL_JSON_BYTES:
                raise OciValidationError("Canonical JSON exceeds the string byte budget")
            _validate_json_node(item, state, depth=depth + 1)
        return
    raise OciValidationError("Canonical JSON contains an unsupported value")


def _validate_oci_limit(
    value: Any,
    name: str,
    *,
    minimum: int | float,
    maximum: int | float,
    allow_float: bool = False,
) -> int | float:
    if value is None:
        raise OciValidationError(f"{name} must be finite and bounded, not None")
    if type(value) is bool:
        raise OciValidationError(f"{name} must be a finite number")
    if not allow_float:
        if type(value) is not int:
            raise OciValidationError(f"{name} must be an exact integer")
    elif type(value) not in (int, float):
        raise OciValidationError(f"{name} must be a finite number")
    if type(value) is float and not math.isfinite(value):
        raise OciValidationError(f"{name} must be finite")
    if value < minimum:
        raise OciValidationError(f"{name} must be at least {minimum}")
    if value > maximum:
        raise OciValidationError(f"{name} must be at most {maximum}")
    return value


def _oci_int_limit(
    value: Any,
    name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    return int(_validate_oci_limit(value, name, minimum=minimum, maximum=maximum))


def _oci_float_limit(
    value: Any,
    name: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    return float(
        _validate_oci_limit(value, name, minimum=minimum, maximum=maximum, allow_float=True)
    )


def _reject_oci_symlink_components(path: Path) -> None:
    candidate = _exact_oci_path(path)
    if not candidate.is_absolute():
        raise OciValidationError("Path is not absolute")
    parent_fd, name = _open_oci_parent_dirfd(candidate)
    try:
        try:
            info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if stat.S_ISLNK(info.st_mode):
            raise OciValidationError("Path contains a symlink component")
    finally:
        os.close(parent_fd)


def _preflight_source(
    source: Path,
    *,
    max_blob_bytes: int,
    start: float,
    deadline_seconds: float,
) -> tuple[int, int, int]:
    """Verify a blob source is a regular non-symlink file within bounds."""
    _require_os_support()
    candidate = _exact_oci_path(source)
    if not candidate.is_absolute():
        raise OciValidationError("Blob source is not an absolute path")
    parent_fd, name = _open_oci_parent_dirfd(candidate)
    try:
        try:
            info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise OciValidationError("Blob source is a symlink")
            fd = os.open(name, _oci_read_flags(), dir_fd=parent_fd)
        except OSError as error:
            raise OciValidationError("Blob source could not be inspected") from error
        try:
            stat_result = os.fstat(fd)
        except Exception:
            os.close(fd)
            raise
        os.close(fd)
    finally:
        os.close(parent_fd)
    if not stat.S_ISREG(stat_result.st_mode):
        raise OciValidationError("Blob source is not a regular file")
    size = stat_result.st_size
    if size > max_blob_bytes:
        raise OciValidationError("Blob source exceeds the per-blob limit")
    _check_deadline(start, deadline_seconds)
    return size, stat_result.st_dev, stat_result.st_ino


def _verify_existing_blob(
    final: Path,
    expected_digest: str,
    *,
    chunk_size: int,
    max_blob_bytes: int,
    start: float,
    deadline_seconds: float,
) -> int:
    """Rehash an existing digest blob with bounded reads under one deadline."""
    _require_os_support()
    parent_fd, name = _open_oci_parent_dirfd(final)
    try:
        try:
            fd = os.open(name, _oci_read_flags(), dir_fd=parent_fd)
        except OSError as error:
            raise OciValidationError("Existing blob could not be opened") from error
    finally:
        os.close(parent_fd)
    actual = 0
    hasher = hashlib.sha256()
    try:
        with os.fdopen(fd, "rb") as handle:
            while True:
                _check_deadline(start, deadline_seconds)
                chunk = handle.read(chunk_size)
                if not chunk:
                    break
                actual += len(chunk)
                if actual > max_blob_bytes:
                    raise OciValidationError("Existing blob exceeds the per-blob limit")
                hasher.update(chunk)
    except Exception:
        raise
    digest = f"sha256:{hasher.hexdigest()}"
    if digest != expected_digest:
        raise OciValidationError("Refusing to replace a mismatched existing blob")
    return actual


def _open_exclusive_nofollow(path: Path) -> int:
    _require_os_support()
    parent_fd, name = _open_oci_parent_dirfd(path)
    try:
        try:
            return os.open(name, _oci_exclusive_flags(), 0o600, dir_fd=parent_fd)
        except FileExistsError:
            raise OciValidationError("Output path already exists") from None
    finally:
        os.close(parent_fd)


def _write_full_fd(fd: int, data: bytes) -> None:
    if type(fd) is not int or type(data) is not bytes:
        raise OciValidationError("write requires an exact integer fd and bytes")
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if type(written) is not int or not 1 <= written <= len(view):
            raise OciValidationError("Short or failed write while building layout")
        view = view[written:]


def _secure_mkdir(path: Path, *, mode: int = 0o700) -> None:
    """Create a directory with nofollow dirfd operations and parent fsync."""
    _require_os_support()
    if type(mode) is not int or not 0 <= mode <= 0o7777:
        raise OciValidationError("directory mode must be an exact bounded integer")
    target = _exact_oci_path(path)
    if not target.is_absolute():
        raise OciValidationError("Layout root is not absolute")
    for part in target.parts:
        if part in ("", ".", ".."):
            raise OciValidationError("Layout root contains an unsafe component")
    fd = os.open(target.anchor, _oci_dir_flags())
    try:
        for part in target.parts[1:]:
            try:
                child_fd = os.open(part, _oci_dir_flags(), dir_fd=fd)
            except FileNotFoundError:
                os.mkdir(part, mode, dir_fd=fd)
                os.chmod(part, mode, dir_fd=fd, follow_symlinks=False)
                os.fsync(fd)
                child_fd = os.open(part, _oci_dir_flags(), dir_fd=fd)
            except OSError as error:
                raise OciValidationError("Layout directory could not be created") from error
            info = os.fstat(child_fd)
            if not stat.S_ISDIR(info.st_mode):
                os.close(child_fd)
                raise OciValidationError("Layout path exists as a non-directory")
            os.close(fd)
            fd = child_fd
    finally:
        os.close(fd)


def _install_bytes_private(final: Path, data: bytes) -> None:
    """Install bytes under ``final`` with exclusive nofollow writes only."""
    _require_os_support()
    parent_fd, name = _open_oci_parent_dirfd(final)
    try:
        try:
            info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            info = None
        if info is not None:
            if not stat.S_ISREG(info.st_mode):
                raise OciValidationError("Existing layout file is unsafe")
            fd = os.open(name, _oci_read_flags(), dir_fd=parent_fd)
            try:
                with os.fdopen(fd, "rb") as handle:
                    existing = handle.read(MAX_CANONICAL_JSON_BYTES + 1)
            except Exception:
                raise
            if existing != data:
                raise OciValidationError(
                    "Refusing to overwrite a mismatched existing layout file"
                ) from None
            return
        temp_name = f".{name}.{uuid.uuid4().hex}.tmp"
        try:
            temp_fd = os.open(temp_name, _oci_exclusive_flags(), 0o600, dir_fd=parent_fd)
        except FileExistsError:
            raise OciValidationError("Output path already exists") from None
        try:
            _write_full_fd(temp_fd, data)
            os.fsync(temp_fd)
        finally:
            os.close(temp_fd)
        try:
            os.link(temp_name, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        except FileExistsError:
            fd = os.open(name, _oci_read_flags(), dir_fd=parent_fd)
            try:
                with os.fdopen(fd, "rb") as handle:
                    existing = handle.read(MAX_CANONICAL_JSON_BYTES + 1)
            except Exception:
                raise
            if existing != data:
                raise OciValidationError(
                    "Refusing to overwrite a mismatched existing layout file"
                ) from None
        finally:
            with suppress(OSError):
                os.unlink(temp_name, dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def _stream_blob_to_layout(
    source: Path,
    blob_root: Path,
    *,
    chunk_size: int,
    max_blob_bytes: int,
    start: float,
    deadline_seconds: float,
    expected_identity: tuple[int, int, int],
) -> tuple[str, int]:
    """Stream one source blob once into a digest-addressed layout blob.

    The source is opened with O_NOFOLLOW and copied in bounded chunks while
    hashing incrementally; the blob is installed with a no-overwrite link and
    the exact preflight identity/size is rechecked after the copy.
    """
    _require_os_support()
    expected_size, expected_dev, expected_ino = expected_identity
    source_fd: int | None = None
    blob_root_fd: int | None = None
    source_parent_fd, source_name = _open_oci_parent_dirfd(source)
    try:
        try:
            source_fd = os.open(source_name, _oci_read_flags(), dir_fd=source_parent_fd)
        except OSError as error:
            raise OciValidationError("Blob source could not be opened") from error
    finally:
        os.close(source_parent_fd)
    blob_root_fd = _open_oci_dirfd(blob_root)
    temp_name = f".tmp-{uuid.uuid4().hex}"
    hasher = hashlib.sha256()
    actual = 0
    writer_fd: int | None = None
    try:
        initial = os.fstat(source_fd)
        if (
            not stat.S_ISREG(initial.st_mode)
            or (initial.st_dev, initial.st_ino) != (expected_dev, expected_ino)
            or initial.st_size != expected_size
        ):
            raise OciValidationError("Blob source changed identity or size")
        writer_fd = os.open(temp_name, _oci_exclusive_flags(), 0o600, dir_fd=blob_root_fd)
        while True:
            _check_deadline(start, deadline_seconds)
            chunk = os.read(source_fd, chunk_size)
            if not chunk:
                break
            actual += len(chunk)
            if actual > max_blob_bytes:
                raise OciValidationError("Blob source exceeded the per-blob limit")
            hasher.update(chunk)
            _write_full_fd(writer_fd, chunk)
        os.fsync(writer_fd)
        final_info = os.fstat(source_fd)
        if (
            (final_info.st_dev, final_info.st_ino) != (expected_dev, expected_ino)
            or final_info.st_size != expected_size
            or actual != expected_size
        ):
            raise OciValidationError("Blob source changed size during copy")
        digest = f"sha256:{hasher.hexdigest()}"
        final = blob_root / digest.removeprefix("sha256:")
        if final.exists():
            _verify_existing_blob(
                final,
                digest,
                chunk_size=chunk_size,
                max_blob_bytes=max_blob_bytes,
                start=start,
                deadline_seconds=deadline_seconds,
            )
            with suppress(OSError):
                os.unlink(temp_name, dir_fd=blob_root_fd)
        else:
            try:
                os.link(
                    temp_name,
                    digest.removeprefix("sha256:"),
                    src_dir_fd=blob_root_fd,
                    dst_dir_fd=blob_root_fd,
                )
            except FileExistsError:
                _verify_existing_blob(
                    final,
                    digest,
                    chunk_size=chunk_size,
                    max_blob_bytes=max_blob_bytes,
                    start=start,
                    deadline_seconds=deadline_seconds,
                )
            finally:
                with suppress(OSError):
                    os.unlink(temp_name, dir_fd=blob_root_fd)
            os.fsync(blob_root_fd)
        return digest, actual
    except Exception:
        if blob_root_fd is not None:
            with suppress(OSError):
                os.unlink(temp_name, dir_fd=blob_root_fd)
        raise
    finally:
        if writer_fd is not None:
            with suppress(OSError):
                os.close(writer_fd)
        if source_fd is not None:
            os.close(source_fd)
        if blob_root_fd is not None:
            os.close(blob_root_fd)


def assemble_oci_layout(
    config: ZanaImageConfig,
    blobs: Mapping[str, Path],
    root: Path,
    *,
    max_blob_bytes: int = MAX_MEMBER_BYTES,
    max_total_blob_bytes: int = MAX_TOTAL_BYTES,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    deadline_seconds: float = 300.0,
) -> OciLayoutResult:
    """Build a deterministic OCI layout rooted at ``root``.

    ``blobs`` maps immutable role names to regular files. Roles outside the
    canonical set and secret/mutable-state members are rejected by the caller
    before assembly through the export safety scanner. Each source blob is
    streamed once in bounded chunks under conservative per-blob and total
    limits; nothing is loaded into memory whole.
    """
    if type(config) is not ZanaImageConfig:
        raise OciValidationError("OCI layout requires an exact ZanaImageConfig model")
    if type(root) is not _CONCRETE_PATH:
        raise OciValidationError("OCI layout root must be an exact concrete pathlib.Path")

    max_blob_bytes = _oci_int_limit(
        max_blob_bytes, "max_blob_bytes", minimum=1, maximum=MAX_VALIDATION_BLOB_BYTES
    )
    max_total_blob_bytes = _oci_int_limit(
        max_total_blob_bytes,
        "max_total_blob_bytes",
        minimum=1,
        maximum=MAX_VALIDATION_TOTAL_BYTES,
    )
    chunk_size = _oci_int_limit(chunk_size, "chunk_size", minimum=1, maximum=MAX_OCI_CHUNK_BYTES)
    deadline_seconds = _oci_float_limit(
        deadline_seconds,
        "deadline_seconds",
        minimum=0.001,
        maximum=MAX_OCI_DEADLINE_SECONDS,
    )
    validate_config_digests(config)
    if type(blobs) is not dict:
        raise OciValidationError("Blob sources must be a builtin mapping")
    _reject_oci_symlink_components(root)

    start = time.monotonic()
    preflight: list[tuple[str, Path, int, tuple[int, int, int]]] = []
    total_preflight = 0
    role_limit = len(ROLE_MEDIA_TYPES)
    collected_roles: list[tuple[str, Path]] = []
    for collected, item in enumerate(blobs.items()):
        if collected >= role_limit + 1:
            raise OciValidationError("Blob role count exceeds the canonical role limit")
        if type(item) is not tuple or len(item) != 2:
            raise OciValidationError("Blob source mapping is malformed")
        role, source = item
        if type(role) is not str:
            raise OciValidationError("Blob role must be an exact string")
        if type(source) is not _CONCRETE_PATH:
            raise OciValidationError("Blob source must be an exact concrete pathlib.Path")
        if role not in ROLE_MEDIA_TYPES:
            raise OciValidationError("Unknown image blob role")
        collected_roles.append(item)
    if len(collected_roles) > role_limit:
        raise OciValidationError("Blob role count exceeds the canonical role limit")
    for role, source in sorted(collected_roles):
        size, dev, ino = _preflight_source(
            source,
            max_blob_bytes=max_blob_bytes,
            start=start,
            deadline_seconds=deadline_seconds,
        )
        total_preflight += size
        if total_preflight > max_total_blob_bytes:
            raise OciValidationError("Total blob size exceeds the configured limit")
        preflight.append((role, source, size, (size, dev, ino)))
    _check_deadline(start, deadline_seconds)

    if root.is_symlink() or not root.is_dir():
        raise OciValidationError("Layout root is not a real directory")
    blob_root = root / "blobs" / "sha256"
    _secure_mkdir(blob_root)
    config_bytes = canonical_json_bytes(config)
    config_digest = digest_bytes(config_bytes)

    layer_descriptors: list[Descriptor] = []
    blob_digests: list[str] = []
    for role, source, size, identity in preflight:
        _check_deadline(start, deadline_seconds)
        digest, copied = _stream_blob_to_layout(
            source,
            blob_root,
            chunk_size=chunk_size,
            max_blob_bytes=max_blob_bytes,
            start=start,
            deadline_seconds=deadline_seconds,
            expected_identity=identity,
        )
        if copied != size:
            raise OciValidationError("Blob source size mismatch after streaming")
        blob_digests.append(digest)
        layer_descriptors.append(
            Descriptor(
                media_type=ROLE_MEDIA_TYPES[role],
                digest=digest,
                size=size,
                annotations=ImmutableAnnotations.from_exact_dict({"org.zana.role": role}),
            )
        )

    _install_bytes_private(blob_root / config_digest.removeprefix("sha256:"), config_bytes)
    manifest = Manifest(
        config=Descriptor(
            media_type=MEDIA_TYPE_ZANA_CONFIG,
            digest=config_digest,
            size=len(config_bytes),
        ),
        layers=tuple(layer_descriptors),
    )
    manifest_bytes = canonical_json_bytes(manifest)
    manifest_digest = digest_bytes(manifest_bytes)
    _check_deadline(start, deadline_seconds)

    index = Index(
        manifests=(
            Descriptor(
                media_type=MEDIA_TYPE_OCI_MANIFEST,
                digest=manifest_digest,
                size=len(manifest_bytes),
            ),
        ),
    )
    index_bytes = canonical_json_bytes(index)
    index_digest = digest_bytes(index_bytes)
    _install_bytes_private(root / "index.json", index_bytes)
    _install_bytes_private(root / "oci-layout", canonical_json_bytes(OciLayoutFile()))
    _install_bytes_private(root / "manifest.json", manifest_bytes)
    _check_deadline(start, deadline_seconds)

    return OciLayoutResult(
        root=root,
        config_digest=config_digest,
        manifest_digest=manifest_digest,
        index_digest=index_digest,
        image_digest=index_digest,
        blob_digests=tuple(blob_digests),
    )


def _remove_quietly(path: Path) -> None:
    try:
        parent_fd, name = _open_oci_parent_dirfd(path)
    except OciValidationError:
        return
    try:
        with suppress(OSError):
            os.unlink(name, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)


def _fsync_directory(path: Path) -> None:
    try:
        fd = _open_oci_dirfd(path)
    except OciValidationError:
        return
    try:
        os.fsync(fd)
    except OSError as error:
        raise OciValidationError("Layout directory fsync failed") from error
    finally:
        os.close(fd)


def _require_digest(value: Any, label: str) -> str:
    try:
        return validate_digest(value)
    except InvalidDigestError:
        raise OciValidationError(f"{label} has an invalid digest") from None


def _verify_file(
    digest: str,
    path: Path,
    label: str,
    *,
    chunk_size: int,
    max_blob_bytes: int,
    max_total_bytes: int,
    total_so_far: int,
    start: float,
    deadline_seconds: float,
) -> int:
    _require_os_support()
    parent_fd, name = _open_oci_parent_dirfd(path)
    try:
        try:
            fd = os.open(name, _oci_read_flags(), dir_fd=parent_fd)
        except FileNotFoundError:
            raise OciValidationError(f"{label} blob is missing") from None
        except OSError as error:
            raise OciValidationError(f"{label} blob could not be opened") from error
    finally:
        os.close(parent_fd)
    hasher = hashlib.sha256()
    total = 0
    with os.fdopen(fd, "rb") as handle:
        initial = os.fstat(handle.fileno())
        if not stat.S_ISREG(initial.st_mode):
            raise OciValidationError(f"{label} blob is not a regular file")
        while True:
            _check_deadline(start, deadline_seconds)
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            total += len(chunk)
            if total > max_blob_bytes:
                raise OciValidationError(f"{label} blob exceeds the per-blob byte limit")
            if total_so_far + total > max_total_bytes:
                raise OciValidationError(f"{label} blob exceeds the total byte limit")
            hasher.update(chunk)
        final_size = os.fstat(handle.fileno()).st_size
    if final_size != initial.st_size:
        raise OciValidationError(f"{label} blob changed size during verification")
    actual = f"sha256:{hasher.hexdigest()}"
    if actual != digest:
        raise OciValidationError(f"{label} digest mismatch")
    return total


def _read_layout_json_blob(
    path: Path,
    label: str,
    *,
    max_json_bytes: int,
    max_blob_bytes: int,
    start: float,
    deadline_seconds: float,
) -> tuple[bytes, str, dict[str, Any]]:
    """Read bounded JSON bytes once; return (data, digest, parsed payload)."""
    _require_os_support()
    _reject_oci_symlink_components(path)
    root_fd = _open_oci_root(path.parent)
    try:
        try:
            info = os.stat(path.name, dir_fd=root_fd, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                raise OciValidationError(f"{label} is not a regular file")
            fd = os.open(
                path.name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=root_fd,
            )
        except FileNotFoundError:
            raise OciValidationError(f"{label} is missing or unsafe") from None
        except OSError as error:
            raise OciValidationError(f"{label} is unreadable") from error
        try:
            with os.fdopen(fd, "rb") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise OciValidationError(f"{label} is not a regular file")
                data = handle.read(max_json_bytes + 1)
        except OSError as error:
            raise OciValidationError(f"{label} is unreadable") from error
    finally:
        os.close(root_fd)
    if len(data) > max_json_bytes:
        raise OciValidationError(f"{label} exceeds the JSON size limit")
    if len(data) > max_blob_bytes:
        raise OciValidationError(f"{label} blob exceeds the per-blob byte limit")
    _check_deadline(start, deadline_seconds)
    digest = f"sha256:{hashlib.sha256(data).hexdigest()}"
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise OciValidationError(f"Malformed {label}") from None
    if type(payload) is not dict:
        raise OciValidationError(f"Malformed {label}")
    return data, digest, payload


def _open_oci_root(root: Path) -> int:
    """Open a root by dirfd and require exact identity after stat."""
    fd = _open_oci_dirfd(root)
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode):
        os.close(fd)
        raise OciValidationError("OCI root is not a directory")
    try:
        stat_path = Path(root).stat(follow_symlinks=False)
    except OSError as error:
        os.close(fd)
        raise OciValidationError("OCI root identity could not be verified") from error
    if (stat_path.st_dev, stat_path.st_ino) != (info.st_dev, info.st_ino):
        os.close(fd)
        raise OciValidationError("OCI root changed identity during scan")
    return fd


def validate_oci_layout(
    root: Path,
    *,
    max_json_bytes: int = MAX_JSON_BYTES_DEFAULT,
    deadline_seconds: float = 300.0,
    max_blob_bytes: int = MAX_VALIDATION_BLOB_BYTES,
    max_total_bytes: int = MAX_VALIDATION_TOTAL_BYTES,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> ValidatedLayout:
    """Validate layout version, JSON shape, descriptors, and every blob digest."""
    _require_os_support()

    root = _exact_oci_path(root)
    max_json_bytes = _oci_int_limit(
        max_json_bytes, "max_json_bytes", minimum=1, maximum=MAX_JSON_BYTES_DEFAULT
    )
    deadline_seconds = _oci_float_limit(
        deadline_seconds,
        "deadline_seconds",
        minimum=0.001,
        maximum=MAX_OCI_DEADLINE_SECONDS,
    )
    max_blob_bytes = _oci_int_limit(
        max_blob_bytes, "max_blob_bytes", minimum=1, maximum=MAX_VALIDATION_BLOB_BYTES
    )
    max_total_bytes = _oci_int_limit(
        max_total_bytes, "max_total_bytes", minimum=1, maximum=MAX_VALIDATION_TOTAL_BYTES
    )
    chunk_size = _oci_int_limit(chunk_size, "chunk_size", minimum=1, maximum=MAX_OCI_CHUNK_BYTES)
    start = time.monotonic()
    _check_deadline(start, deadline_seconds)
    _reject_oci_symlink_components(Path(root))
    if root.is_symlink() or not root.is_dir():
        raise OciValidationError("OCI layout root is not a real directory")
    _layout_bytes, _layout_digest, layout = _read_layout_json_blob(
        root / "oci-layout",
        "oci-layout",
        max_json_bytes=max_json_bytes,
        max_blob_bytes=max_blob_bytes,
        start=start,
        deadline_seconds=deadline_seconds,
    )
    _check_deadline(start, deadline_seconds)
    if (
        layout.get("imageLayoutVersion") != OCI_LAYOUT_VERSION
        and layout.get("image_layout_version") != OCI_LAYOUT_VERSION
    ):
        raise OciValidationError("Unsupported OCI layout version")

    index_bytes, index_digest, index_payload = _read_layout_json_blob(
        root / "index.json",
        "index.json",
        max_json_bytes=max_json_bytes,
        max_blob_bytes=max_blob_bytes,
        start=start,
        deadline_seconds=deadline_seconds,
    )
    _check_deadline(start, deadline_seconds)
    try:
        index = Index.model_validate(index_payload)
    except Exception:
        raise OciValidationError("Malformed OCI index") from None
    if index.schema_version != 2 or index.media_type != MEDIA_TYPE_OCI_INDEX:
        raise OciValidationError("Unsupported OCI index schema or media type.")
    if len(index.manifests) != 1:
        raise OciValidationError("ZANA imports require exactly one image manifest.")

    manifest_descriptor = index.manifests[0]
    if manifest_descriptor.media_type != MEDIA_TYPE_OCI_MANIFEST:
        raise OciValidationError("Index descriptor is not an OCI image manifest.")
    manifest_digest = _require_digest(manifest_descriptor.digest, "manifest")
    manifest_path = root / "manifest.json"
    manifest_bytes, manifest_actual_digest, manifest_payload = _read_layout_json_blob(
        manifest_path,
        "manifest",
        max_json_bytes=max_json_bytes,
        max_blob_bytes=max_blob_bytes,
        start=start,
        deadline_seconds=deadline_seconds,
    )
    manifest_size = len(manifest_bytes)
    if manifest_actual_digest != manifest_digest:
        raise OciValidationError("Manifest digest mismatch")
    if manifest_size != manifest_descriptor.size:
        raise OciValidationError("Manifest descriptor size does not match the file.")
    _check_deadline(start, deadline_seconds)
    try:
        manifest = Manifest.model_validate(manifest_payload)
    except Exception:
        raise OciValidationError("Malformed OCI manifest") from None
    if manifest.schema_version != 2 or manifest.media_type != MEDIA_TYPE_OCI_MANIFEST:
        raise OciValidationError("Unsupported OCI manifest schema or media type.")
    config_descriptor = manifest.config
    if config_descriptor.media_type != MEDIA_TYPE_ZANA_CONFIG:
        raise OciValidationError("Unsupported config media type")
    config_digest = _require_digest(config_descriptor.digest, "config")
    config_path = root / "blobs" / "sha256" / config_digest.removeprefix("sha256:")
    config_bytes, config_actual_digest, config_payload = _read_layout_json_blob(
        config_path,
        "config",
        max_json_bytes=max_json_bytes,
        max_blob_bytes=max_blob_bytes,
        start=start,
        deadline_seconds=deadline_seconds,
    )
    config_size = len(config_bytes)
    if config_actual_digest != config_digest:
        raise OciValidationError("Config digest mismatch")
    if config_size != config_descriptor.size:
        raise OciValidationError("Config descriptor size does not match the file.")
    _check_deadline(start, deadline_seconds)
    secret_hits = scan_payload_for_secrets(config_payload)
    if secret_hits:
        raise OciValidationError("Image config would serialize secret values")
    try:
        config = ZanaImageConfig.model_validate(config_payload)
    except Exception:
        raise OciValidationError("Unsupported or malformed ZANA image config") from None
    validate_config_digests(config)

    blob_digests: list[str] = []
    seen_blob_digests: set[str] = set()
    total_size = len(index_bytes) + len(_layout_bytes) + manifest_size + config_size
    for descriptor in manifest.layers:
        if descriptor.media_type not in set(ROLE_MEDIA_TYPES.values()):
            raise OciValidationError("Unsupported blob media type")
        digest = _require_digest(descriptor.digest, "layer")
        if digest in seen_blob_digests:
            raise OciValidationError("Duplicate OCI layer digest rejected")
        seen_blob_digests.add(digest)
        path = root / "blobs" / "sha256" / digest.removeprefix("sha256:")
        size = _verify_file(
            digest,
            path,
            "layer",
            chunk_size=chunk_size,
            max_blob_bytes=max_blob_bytes,
            max_total_bytes=max_total_bytes,
            total_so_far=total_size,
            start=start,
            deadline_seconds=deadline_seconds,
        )
        if size != descriptor.size:
            raise OciValidationError("Layer descriptor size does not match the file.")
        blob_digests.append(digest)
        total_size += size
        if total_size > max_total_bytes:
            raise OciValidationError("OCI total byte limit exceeded")
        _check_deadline(start, deadline_seconds)

    return ValidatedLayout(
        root=root,
        config=config,
        config_digest=config_digest,
        manifest_digest=manifest_digest,
        index_digest=index_digest,
        blob_digests=tuple(blob_digests),
        total_size=total_size,
    )


def _check_deadline(start: float, deadline_seconds: float | None) -> None:
    if deadline_seconds is not None and time.monotonic() - start > deadline_seconds:
        raise OciValidationError("OCI layout validation exceeded the deadline.")
