"""Instance export/import schema reservation; secrets are never serialized."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from zana_core.memory.models import ImagePointer, InstancePointer, MutableInstanceState

EXPORT_SCHEMA_VERSION = 1

SECRET_KEY_PATTERNS: tuple[str, ...] = (
    "accesskey",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "passwd",
    "private_key",
    "secret",
    "token",
)


def _normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def is_sensitive_key(key: str) -> bool:
    """Whether a payload key can carry a serialized secret value."""
    normalized = _normalize_key(key)
    return any(pattern in normalized for pattern in SECRET_KEY_PATTERNS)


def scan_for_secret_values(payload: dict[str, Any]) -> list[str]:
    """Return paths of non-empty string values under sensitive keys.

    Used defensively in tests and by import validation to prove exports never
    serialize secrets; the export schema itself has no secret value fields.
    """
    hits: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                child = f"{path}.{key}" if path else key
                if is_sensitive_key(key) and isinstance(value, str) and value:
                    hits.append(child)
                walk(value, child)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(payload, "")
    return hits


class SecretRequirement(BaseModel):
    """Reference to a required secret; the value is never part of the schema."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1)
    required: bool = True
    description: str | None = None
    resolved: bool = False


class InstanceExportEnvelope(BaseModel):
    """Versioned instance export carrying state but never secret values."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = EXPORT_SCHEMA_VERSION
    exported_at: datetime
    instance_id: str
    image: ImagePointer
    snapshot_revision: int
    state: MutableInstanceState
    secret_requirements: list[SecretRequirement] = Field(default_factory=list)
    contains_secret_values: bool = False

    @model_validator(mode="after")
    def _reject_secret_values(self) -> InstanceExportEnvelope:
        if self.contains_secret_values:
            raise ValueError("exports must never serialize secret values")
        resolved = sorted(
            requirement.key for requirement in self.secret_requirements if requirement.resolved
        )
        if resolved:
            raise ValueError(f"resolved secret requirements cannot be exported: {resolved}")
        return self


class UnsupportedExportSchemaError(ValueError):
    """The import envelope uses an unsupported schema version."""


class InstanceImportEnvelope(BaseModel):
    """Versioned instance import; secrets arrive only as unresolved references."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int
    exported_at: datetime
    instance_id: str
    image: ImagePointer
    snapshot_revision: int
    state: MutableInstanceState
    secret_requirements: list[SecretRequirement] = Field(default_factory=list)
    unresolved_requirements: list[str] = Field(default_factory=list)
    contains_secret_values: bool = False

    @model_validator(mode="after")
    def _supported_schema(self) -> InstanceImportEnvelope:
        if self.schema_version != EXPORT_SCHEMA_VERSION:
            raise ValueError(f"unsupported export schema version {self.schema_version}")
        if self.contains_secret_values:
            raise ValueError("imports must never contain secret values")
        return self

    def secret_requirement_keys(self) -> list[str]:
        """All unresolved secret references in deterministic order."""
        return sorted(
            {requirement.key for requirement in self.secret_requirements}
            | set(self.unresolved_requirements)
        )


def build_instance_export(
    instance: InstancePointer,
    state: MutableInstanceState,
    secret_requirements: Sequence[SecretRequirement] = (),
    *,
    clock: Callable[[], datetime] | None = None,
) -> InstanceExportEnvelope:
    """Build an export referencing image state without serializing secrets."""
    for requirement in secret_requirements:
        if requirement.resolved:
            raise ValueError(
                f"secret requirement {requirement.key} is resolved; "
                "values are never serialized in exports"
            )
    ordered = sorted(secret_requirements, key=lambda requirement: requirement.key)
    return InstanceExportEnvelope(
        exported_at=(clock() if clock else datetime.now(UTC)),
        instance_id=instance.instance_id,
        image=instance.image,
        snapshot_revision=instance.snapshot_revision,
        state=state.model_copy(deep=True),
        secret_requirements=list(ordered),
        contains_secret_values=False,
    )


def build_import_envelope(payload: dict[str, Any]) -> InstanceImportEnvelope:
    """Validate an import payload and reject unsupported schema versions."""
    if payload.get("schema_version") != EXPORT_SCHEMA_VERSION:
        version = payload.get("schema_version")
        raise UnsupportedExportSchemaError(f"unsupported export schema version {version!r}")
    return InstanceImportEnvelope.model_validate(payload)
