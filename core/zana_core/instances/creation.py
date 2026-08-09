"""Instance creation binding immutable image config and mutable state."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from zana_core.domain.enums import InstanceStatus
from zana_core.images.models import (
    Adapter,
    Behavior,
    KnowledgeSnapshot,
    ZanaImageConfig,
)
from zana_core.images.models import (
    Permissions as ImagePermissions,
)
from zana_core.instances.errors import InstanceError
from zana_core.instances.models import (
    GenerationSettings,
    InstanceConfig,
    InstanceRecord,
    LowResourceLimits,
)
from zana_core.memory.models import (
    ImagePointer,
    InstancePointer,
    MutableInstanceState,
)
from zana_core.runtimes.base import ModelDescriptor, RuntimeDescriptor


class NotRunnableImageError(InstanceError):
    """The image is not runnable and cannot create an instance."""


class UnresolvedArtifactError(InstanceError):
    """Required immutable artifact digests are not available."""


class UnresolvedSecretError(InstanceError):
    """Required secret references are not resolved."""


def declared_artifact_digests(config: ZanaImageConfig) -> list[str]:
    """Return every immutable digest the image config requires at runtime."""
    digests: list[str] = []

    def add(value: str | None) -> None:
        if value is not None:
            digests.append(value)

    behavior: Behavior | None = config.behavior
    if behavior is not None:
        add(behavior.system_policy_digest)
        add(behavior.behavior_digest)
    knowledge: KnowledgeSnapshot | None = config.knowledge
    if knowledge is not None:
        add(knowledge.snapshot_digest)
        add(knowledge.embedding_model_digest)
        if knowledge.chunker is not None:
            add(knowledge.chunker.config_digest)
    adapter: Adapter | None = config.adapter
    if adapter is not None:
        add(adapter.digest)
        add(adapter.base_model_digest)
        add(adapter.training_config_digest)
        add(adapter.dataset_digest)
    image_permissions: ImagePermissions = config.permissions
    add(image_permissions.digest)
    add(config.evaluation.suite_digest)
    add(config.evaluation.report_digest)
    add(config.build.build_plan_digest)
    for tool in config.tools:
        add(tool.digest)
    return sorted(set(digests))


class InstanceCreationService:
    """Validate image preconditions and bind immutable/mutable instance halves."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))

    def create(
        self,
        *,
        config: ZanaImageConfig,
        image_digest: str,
        runtimes: Sequence[RuntimeDescriptor],
        models: Sequence[ModelDescriptor],
        available_artifacts: set[str],
        resolved_secrets: set[str],
        instance_name: str | None = None,
        instance_id: str | None = None,
        context_token_budget: int = 4096,
        low_resource_limits: LowResourceLimits | None = None,
    ) -> InstanceRecord:
        """Create an instance only when every start precondition is provable.

        The immutable image config/digest and the mutable instance pointer and
        state are stored as separate records. Missing or weak exact base
        identity, unresolved artifacts, and unresolved secret requirements are
        all fail-closed errors; nothing is guessed or substituted.
        """
        available_base_digests = {model.digest for model in models if model.digest}
        runnability = config.runnability(available_base_digests)
        if runnability.state.value != "runnable":
            raise NotRunnableImageError(
                f"Image {image_digest} is {runnability.state.value}: {runnability.reason}"
            )
        if config.base_model.identity_digest is None:
            raise NotRunnableImageError(
                "Image has no exact base model digest; instances cannot start safely."
            )

        missing_artifacts = [
            digest
            for digest in declared_artifact_digests(config)
            if digest not in available_artifacts
        ]
        if missing_artifacts:
            raise UnresolvedArtifactError(
                "Required image artifacts are unresolved: " + ", ".join(sorted(missing_artifacts))
            )

        missing_secrets = [
            reference
            for reference in config.permissions.secrets_allow
            if reference not in resolved_secrets
        ]
        if missing_secrets:
            raise UnresolvedSecretError(
                "Required secret references are unresolved: " + ", ".join(sorted(missing_secrets))
            )

        now = self._clock()
        resolved_id = instance_id or f"instance-{now.timestamp():.0f}"
        image_pointer = ImagePointer(digest=image_digest, schema_version=1)
        pointer = InstancePointer(
            instance_id=resolved_id,
            image=image_pointer,
            snapshot_revision=0,
            state_schema_version=1,
            updated_at=now,
        )
        state = MutableInstanceState(
            instance_id=resolved_id,
            state_revision=0,
            updated_at=now,
        )
        runtime_compatibility = tuple(config.base_model.runtime_compatibility)
        required_capabilities = tuple(config.base_model.required_capabilities)
        instance_config = InstanceConfig(
            instance_name=instance_name or f"{config.name}-{resolved_id}",
            image_digest=image_digest,
            image_name=config.name,
            image_version=config.version,
            base_model_digest=config.base_model.identity_digest,
            required_runtime_compatibility=runtime_compatibility,
            required_capabilities=required_capabilities,
            required_artifact_digests=tuple(declared_artifact_digests(config)),
            required_secret_references=tuple(sorted(config.permissions.secrets_allow)),
            knowledge_snapshot_digest=(
                config.knowledge.snapshot_digest if config.knowledge else None
            ),
            tool_ids=tuple(tool.id for tool in config.tools),
            context_token_budget=context_token_budget,
            low_resource_limits=low_resource_limits or LowResourceLimits(),
            generation_settings=GenerationSettings(),
        )
        return InstanceRecord(
            instance_id=resolved_id,
            config=instance_config,
            pointer=pointer,
            state=state,
            status=InstanceStatus.STOPPED,
            binding=None,
            last_error=None,
            updated_at=now,
        )
