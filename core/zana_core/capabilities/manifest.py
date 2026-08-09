"""Canonical zana.yaml Capability Source manifest model and safe YAML loading."""

from __future__ import annotations

# Field names mirror the spec-mandated zana.yaml keys, so N815 is intentionally disabled.
# ruff: noqa: N815
import re
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

SEMVER_PATTERN = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)

ID_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")

# Character set shared by runtime validation and the JSON Schemas.  Paths are
# project-root-relative, forward-slash separated, and never absolute.
SAFE_PATH_CHARSET = re.compile(r"^[A-Za-z0-9._/ -]+$")

SUPPORTED_SCHEMA_VERSION = 1
SUPPORTED_KIND = "ZanaCapability"


class DuplicateKeyError(Exception):
    """Raised when YAML declares the same mapping key more than once."""

    def __init__(self, key: Any, line: int) -> None:
        self.key = key
        self.line = line
        super().__init__(f"duplicate key {key!r} at line {line}")


class _DuplicateKeySafeLoader(yaml.SafeLoader):
    """SafeLoader that fails on duplicate keys instead of silently overwriting."""


def _construct_mapping(
    loader: _DuplicateKeySafeLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise DuplicateKeyError(key, key_node.start_mark.line + 1)
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_DuplicateKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)


def parse_safe_yaml(text: str) -> list[Any]:
    """Parse all YAML documents with a safe, duplicate-key-rejecting loader."""
    return list(yaml.load_all(text, Loader=_DuplicateKeySafeLoader))


def validate_semver(value: str) -> str:
    if not SEMVER_PATTERN.fullmatch(value):
        raise ValueError(f"expected strict semver X.Y.Z[-prerelease][+build], got {value!r}")
    return value


def validate_manifest_id(value: str) -> str:
    if not ID_PATTERN.fullmatch(value):
        raise ValueError(
            "id must use letters, digits, dots, hyphens, or underscores and "
            "start and end with a letter or digit"
        )
    return value


def validate_safe_path(value: str | None) -> str | None:
    if value is None:
        return None
    if not value:
        raise ValueError("path must not be empty")
    if value != value.strip():
        raise ValueError("path must not have leading or trailing whitespace")
    if value.startswith("/") or "\\" in value:
        raise ValueError("path must be project-root-relative without drive or backslash separators")
    if not SAFE_PATH_CHARSET.fullmatch(value):
        raise ValueError("path contains characters outside the safe project-relative charset")
    return value


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Compatibility(_StrictModel):
    minimumContextTokens: int | None = Field(default=None, ge=0)
    requiredModelCapabilities: list[str] | None = Field(default=None, min_length=1)


class Goal(_StrictModel):
    type: str = Field(min_length=1, max_length=200)
    primaryMetrics: list[str] | None = Field(default=None, min_length=1)


class Behavior(_StrictModel):
    system: str

    _system = field_validator("system")(validate_safe_path)


class KnowledgeSource(_StrictModel):
    path: str

    _path = field_validator("path")(validate_safe_path)


class Knowledge(_StrictModel):
    sources: list[KnowledgeSource] | None = None
    citationRequired: bool | None = None


class Training(_StrictModel):
    optional: bool = False
    goal: str | None = Field(default=None, min_length=1, max_length=200)
    train: str | None = None
    validation: str | None = None
    minimumExamples: int | None = Field(default=None, ge=0)

    _train = field_validator("train")(validate_safe_path)
    _validation = field_validator("validation")(validate_safe_path)


class Tools(_StrictModel):
    manifest: str

    _manifest = field_validator("manifest")(validate_safe_path)


class Permissions(_StrictModel):
    policy: str

    _policy = field_validator("policy")(validate_safe_path)


class DomainGate(_StrictModel):
    minimumAbsolute: float | None = Field(default=None, ge=0, le=1)
    minimumImprovement: float | None = Field(default=None, ge=0)


class RegressionGate(_StrictModel):
    maximumDrop: float | None = Field(default=None, ge=0)


class VerificationGates(_StrictModel):
    domain: DomainGate | None = None
    regression: RegressionGate | None = None


class Verification(_StrictModel):
    gates: VerificationGates | None = None


class Evaluation(_StrictModel):
    domain: str | None = None
    regression: str | None = None

    _domain = field_validator("domain")(validate_safe_path)
    _regression = field_validator("regression")(validate_safe_path)


class CapabilityManifest(_StrictModel):
    """Canonical editable capability manifest (zana.yaml, schemaVersion 1)."""

    schemaVersion: int
    kind: Literal["ZanaCapability"]
    id: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=300)
    version: str
    description: str | None = Field(default=None, max_length=4000)
    license: str | None = Field(default=None, max_length=1000)
    compatibility: Compatibility | None = None
    goal: Goal | None = None
    behavior: Behavior | None = None
    knowledge: Knowledge | None = None
    training: Training | None = None
    tools: Tools | None = None
    permissions: Permissions | None = None
    evaluation: Evaluation | None = None
    verification: Verification | None = None

    _id = field_validator("id")(validate_manifest_id)
    _version = field_validator("version")(validate_semver)
