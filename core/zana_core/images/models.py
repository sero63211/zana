"""Versioned ZANA Image configuration models."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from zana_core.artifacts.digest import validate_digest


class RunnableState(str, Enum):
    """Explicit runnability state for an imported or built ZANA Image."""

    RUNNABLE = "runnable"
    NOT_RUNNABLE_MISSING_BASE = "not-runnable-missing-base"
    NOT_RUNNABLE_WEAK_IDENTITY = "not-runnable-weak-identity"
    NOT_RUNNABLE_UNKNOWN = "not-runnable-unknown"


class ImageRunnability(BaseModel):
    """Typed runnability result with a machine-readable reason."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    state: RunnableState
    reason: str = Field(default="", max_length=1000)
    exact_base_digest: str | None = Field(default=None, max_length=200)

    @field_validator("exact_base_digest")
    @classmethod
    def validate_exact_base_digest(cls, value: str | None) -> str | None:
        return _validated_digest(value)


def _validated_digest(value: str | None) -> str | None:
    if value is None:
        return None
    return validate_digest(value)


class BaseModelReference(BaseModel):
    """Exact base-model identity requirement.

    An image is runnable only when ``identity_digest`` is present and the
    digest exists locally. Missing or weak identity is explicit state, never a
    silent substitution.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    display_name: str | None = Field(default=None, max_length=500)
    family: str | None = Field(default=None, max_length=200)
    identity_digest: str | None = Field(default=None, description="Canonical sha256 digest")
    runtime_compatibility: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    required_capabilities: tuple[str, ...] = Field(default_factory=tuple, max_length=32)

    @field_validator("runtime_compatibility", "required_capabilities")
    @classmethod
    def validate_list_items(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if type(item) is not str or len(item) > 200:
                raise ValueError("capability/runtime item exceeds the length limit")
        return value

    @field_validator("runtime_compatibility", "required_capabilities", mode="before")
    @classmethod
    def require_exact_sequence(cls, value: object) -> tuple[str, ...]:
        if type(value) not in (list, tuple):
            raise ValueError("capability/runtime list must be a builtin list or tuple")
        return tuple(value)  # type: ignore[arg-type]

    @field_validator("identity_digest")
    @classmethod
    def validate_identity_digest(cls, value: str | None) -> str | None:
        return _validated_digest(value)

    def runnability(self, available_base_digests: set[str] | None = None) -> ImageRunnability:
        if self.identity_digest is None:
            return ImageRunnability(
                state=RunnableState.NOT_RUNNABLE_WEAK_IDENTITY,
                reason="Base model identity has no exact digest.",
            )
        available = available_base_digests
        if available is not None and self.identity_digest not in available:
            return ImageRunnability(
                state=RunnableState.NOT_RUNNABLE_MISSING_BASE,
                reason="Exact base model digest is not available locally.",
                exact_base_digest=self.identity_digest,
            )
        return ImageRunnability(
            state=RunnableState.RUNNABLE,
            reason="Exact base model digest is available.",
            exact_base_digest=self.identity_digest,
        )


class Behavior(BaseModel):
    """Immutable behavior policy bound to a content digest."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    system_policy_digest: str | None = Field(default=None)
    behavior_digest: str | None = Field(default=None)

    @field_validator("system_policy_digest", "behavior_digest")
    @classmethod
    def validate_digests(cls, value: str | None) -> str | None:
        return _validated_digest(value)


class Chunker(BaseModel):
    """Deterministic knowledge chunking configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: str = Field(default="zana.heading-aware", max_length=200)
    version: int = Field(default=1, ge=1, le=1000)
    config_digest: str | None = Field(default=None)

    @field_validator("config_digest")
    @classmethod
    def validate_config_digest(cls, value: str | None) -> str | None:
        return _validated_digest(value)


class KnowledgeSnapshot(BaseModel):
    """Immutable knowledge snapshot references."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    snapshot_digest: str | None = Field(default=None)
    embedding_model_identity: str | None = Field(default=None, max_length=500)
    embedding_model_digest: str | None = Field(default=None)
    chunker: Chunker | None = None

    @field_validator("snapshot_digest", "embedding_model_digest")
    @classmethod
    def validate_digests(cls, value: str | None) -> str | None:
        return _validated_digest(value)


class Adapter(BaseModel):
    """Optional parameter-efficient trained artifact bound to a base model."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    type: str = Field(default="lora", max_length=200)
    digest: str | None = Field(default=None)
    base_model_digest: str | None = Field(default=None)
    training_provider: str | None = Field(default=None, max_length=200)
    training_config_digest: str | None = Field(default=None)
    dataset_digest: str | None = Field(default=None)

    @field_validator(
        "digest",
        "base_model_digest",
        "training_config_digest",
        "dataset_digest",
    )
    @classmethod
    def validate_digests(cls, value: str | None) -> str | None:
        return _validated_digest(value)


class Tool(BaseModel):
    """Built-in or explicitly approved tool reference."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: str = Field(min_length=1, max_length=200)
    version: int = Field(default=1, ge=1, le=1000)
    digest: str | None = Field(default=None)

    @field_validator("digest")
    @classmethod
    def validate_digest(cls, value: str | None) -> str | None:
        return _validated_digest(value)


class Permissions(BaseModel):
    """Default-deny image permissions matching the ZANA security spec."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    digest: str | None = Field(default=None)
    network_outbound: bool = False
    filesystem_read: tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    filesystem_write: tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    tools_allow: tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    secrets_allow: tuple[str, ...] = Field(default_factory=tuple, max_length=64)

    @field_validator("filesystem_read", "filesystem_write", "tools_allow", "secrets_allow")
    @classmethod
    def validate_permission_items(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if type(item) is not str or len(item) > 500:
                raise ValueError("permission list item exceeds the length limit")
        return value

    @field_validator(
        "filesystem_read", "filesystem_write", "tools_allow", "secrets_allow", mode="before"
    )
    @classmethod
    def require_exact_sequence(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if type(value) not in (list, tuple):
            raise ValueError("permission list must be a builtin list or tuple")
        return tuple(value)  # type: ignore[arg-type]

    @field_validator("digest")
    @classmethod
    def validate_digest(cls, value: str | None) -> str | None:
        return _validated_digest(value)


class Evaluation(BaseModel):
    """Verification suite and report references."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    suite_digest: str | None = Field(default=None)
    report_digest: str | None = Field(default=None)
    status: str = Field(default="unverified", max_length=100)

    @field_validator("suite_digest", "report_digest")
    @classmethod
    def validate_digests(cls, value: str | None) -> str | None:
        return _validated_digest(value)


class BuildMetadata(BaseModel):
    """Build provenance for an immutable image."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    zana_version: str = Field(default="0.1.0", max_length=100)
    build_plan_digest: str | None = Field(default=None)
    built_at: str | None = Field(default=None, max_length=100)

    @field_validator("build_plan_digest")
    @classmethod
    def validate_digest(cls, value: str | None) -> str | None:
        return _validated_digest(value)


class ZanaImageConfig(BaseModel):
    """Versioned logical configuration for an immutable ZANA Image."""

    model_config = ConfigDict(
        alias_generator=None, populate_by_name=True, extra="forbid", frozen=True, strict=True
    )

    schema_version: Literal[1] = Field(
        default=1,
        validation_alias=AliasChoices("schemaVersion", "schema_version"),
        serialization_alias="schemaVersion",
    )
    kind: Literal["ZanaImage"] = Field(
        default="ZanaImage",
        validation_alias=AliasChoices("kind", "kind"),
        serialization_alias="kind",
    )
    name: str = Field(min_length=1, max_length=300)
    version: str = Field(min_length=1, max_length=100)
    base_model: BaseModelReference
    behavior: Behavior | None = None
    knowledge: KnowledgeSnapshot | None = None
    adapter: Adapter | None = None
    tools: tuple[Tool, ...] = Field(default_factory=tuple, max_length=128)
    permissions: Permissions = Field(default_factory=Permissions)
    evaluation: Evaluation = Field(default_factory=Evaluation)
    build: BuildMetadata = Field(default_factory=BuildMetadata)

    @field_validator("tools", mode="before")
    @classmethod
    def require_exact_sequence(cls, value: object) -> tuple[object, ...]:
        if type(value) not in (list, tuple):
            raise ValueError("tools must be a builtin list or tuple")
        return tuple(value)  # type: ignore[arg-type]

    def runnability(self, available_base_digests: set[str] | None = None) -> ImageRunnability:
        return self.base_model.runnability(available_base_digests)


def validate_config_digests(config: ZanaImageConfig) -> None:
    """Validate every declared digest field, including nested optionals."""
    if type(config) is not ZanaImageConfig:
        raise ValueError("config must be an exact ZanaImageConfig model")
    raw = config.__dict__
    if type(raw) is not dict:
        raise ValueError("config raw state is invalid")
    base = raw.get("base_model")
    if type(base) is not BaseModelReference:
        raise ValueError("config base model is invalid")
    candidates = [base.identity_digest]
    for field_name, model_type in (
        ("behavior", Behavior),
        ("knowledge", KnowledgeSnapshot),
        ("adapter", Adapter),
    ):
        nested = raw.get(field_name)
        if nested is not None:
            if type(nested) is not model_type:
                raise ValueError(f"config {field_name} is invalid")
            nested_raw = nested.__dict__
            if type(nested_raw) is not dict:
                raise ValueError(f"config {field_name} raw state is invalid")
            if field_name == "behavior":
                candidates.extend(
                    [
                        nested_raw.get("system_policy_digest"),
                        nested_raw.get("behavior_digest"),
                    ]
                )
            elif field_name == "knowledge":
                candidates.extend(
                    [
                        nested_raw.get("snapshot_digest"),
                        nested_raw.get("embedding_model_digest"),
                    ]
                )
                chunker = nested_raw.get("chunker")
                if chunker is not None:
                    if type(chunker) is not Chunker:
                        raise ValueError("config chunker is invalid")
                    chunker_raw = chunker.__dict__
                    if type(chunker_raw) is not dict:
                        raise ValueError("config chunker raw state is invalid")
                    candidates.append(chunker_raw.get("config_digest"))
            else:
                for digest_field in (
                    "digest",
                    "base_model_digest",
                    "training_config_digest",
                    "dataset_digest",
                ):
                    candidates.append(nested_raw.get(digest_field))
    permissions = raw.get("permissions")
    if type(permissions) is not Permissions:
        raise ValueError("config permissions are invalid")
    candidates.append(permissions.__dict__.get("digest"))
    evaluation = raw.get("evaluation")
    if type(evaluation) is not Evaluation:
        raise ValueError("config evaluation is invalid")
    candidates.extend(
        [
            evaluation.__dict__.get("suite_digest"),
            evaluation.__dict__.get("report_digest"),
        ]
    )
    build = raw.get("build")
    if type(build) is not BuildMetadata:
        raise ValueError("config build metadata is invalid")
    candidates.append(build.__dict__.get("build_plan_digest"))
    for digest in candidates:
        if digest is not None:
            if type(digest) is not str:
                raise ValueError("config digest field is invalid")
            validate_digest(digest)
    tools = raw.get("tools")
    if type(tools) is not tuple or len(tools) > 128:
        raise ValueError("config tools are invalid")
    tool_digests: list[str | None] = []
    for tool in tools:  # type: ignore[union-attr]
        if type(tool) is not Tool:
            raise ValueError("config tools are invalid")
        tool_digests.append(tool.__dict__.get("digest"))
    for digest in tool_digests:
        if digest is not None:
            if type(digest) is not str:
                raise ValueError("config tool digest is invalid")
            validate_digest(digest)
