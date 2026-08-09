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

    state: RunnableState
    reason: str = ""
    exact_base_digest: str | None = None


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

    display_name: str | None = Field(default=None, max_length=500)
    family: str | None = Field(default=None, max_length=200)
    identity_digest: str | None = Field(default=None, description="Canonical sha256 digest")
    runtime_compatibility: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)

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

    system_policy_digest: str | None = Field(default=None)
    behavior_digest: str | None = Field(default=None)

    @field_validator("system_policy_digest", "behavior_digest")
    @classmethod
    def validate_digests(cls, value: str | None) -> str | None:
        return _validated_digest(value)


class Chunker(BaseModel):
    """Deterministic knowledge chunking configuration."""

    id: str = Field(default="zana.heading-aware", max_length=200)
    version: int = 1
    config_digest: str | None = Field(default=None)

    @field_validator("config_digest")
    @classmethod
    def validate_config_digest(cls, value: str | None) -> str | None:
        return _validated_digest(value)


class KnowledgeSnapshot(BaseModel):
    """Immutable knowledge snapshot references."""

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

    type: str = "lora"
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

    id: str = Field(min_length=1, max_length=200)
    version: int = 1
    digest: str | None = Field(default=None)

    @field_validator("digest")
    @classmethod
    def validate_digest(cls, value: str | None) -> str | None:
        return _validated_digest(value)


class Permissions(BaseModel):
    """Default-deny image permissions matching the ZANA security spec."""

    digest: str | None = Field(default=None)
    network_outbound: bool = False
    filesystem_read: list[str] = Field(default_factory=list)
    filesystem_write: list[str] = Field(default_factory=list)
    tools_allow: list[str] = Field(default_factory=list)
    secrets_allow: list[str] = Field(default_factory=list)

    @field_validator("digest")
    @classmethod
    def validate_digest(cls, value: str | None) -> str | None:
        return _validated_digest(value)


class Evaluation(BaseModel):
    """Verification suite and report references."""

    suite_digest: str | None = Field(default=None)
    report_digest: str | None = Field(default=None)
    status: str = "unverified"

    @field_validator("suite_digest", "report_digest")
    @classmethod
    def validate_digests(cls, value: str | None) -> str | None:
        return _validated_digest(value)


class BuildMetadata(BaseModel):
    """Build provenance for an immutable image."""

    zana_version: str = "0.1.0"
    build_plan_digest: str | None = Field(default=None)
    built_at: str | None = Field(default=None)

    @field_validator("build_plan_digest")
    @classmethod
    def validate_digest(cls, value: str | None) -> str | None:
        return _validated_digest(value)


class ZanaImageConfig(BaseModel):
    """Versioned logical configuration for an immutable ZANA Image."""

    model_config = ConfigDict(alias_generator=None, populate_by_name=True)

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
    tools: list[Tool] = Field(default_factory=list)
    permissions: Permissions = Field(default_factory=Permissions)
    evaluation: Evaluation = Field(default_factory=Evaluation)
    build: BuildMetadata = Field(default_factory=BuildMetadata)

    def runnability(self, available_base_digests: set[str] | None = None) -> ImageRunnability:
        return self.base_model.runnability(available_base_digests)


def validate_config_digests(config: ZanaImageConfig) -> None:
    """Validate every declared digest field, including nested optionals."""

    candidates = [
        config.base_model.identity_digest,
        config.behavior.system_policy_digest if config.behavior else None,
        config.behavior.behavior_digest if config.behavior else None,
        config.knowledge.snapshot_digest if config.knowledge else None,
        config.knowledge.embedding_model_digest if config.knowledge else None,
        config.knowledge.chunker.config_digest
        if config.knowledge and config.knowledge.chunker
        else None,
        config.adapter.digest if config.adapter else None,
        config.adapter.base_model_digest if config.adapter else None,
        config.adapter.training_config_digest if config.adapter else None,
        config.adapter.dataset_digest if config.adapter else None,
        config.permissions.digest,
        config.evaluation.suite_digest,
        config.evaluation.report_digest,
        config.build.build_plan_digest,
    ]
    for digest in candidates:
        if digest is not None:
            validate_digest(digest)
    for tool in config.tools:
        if tool.digest is not None:
            validate_digest(tool.digest)
