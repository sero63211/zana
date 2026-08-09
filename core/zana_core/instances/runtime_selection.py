"""Exact runtime/model compatibility selection for instance starts."""

from __future__ import annotations

from collections.abc import Sequence

from zana_core.images.models import ZanaImageConfig
from zana_core.instances.errors import InstanceError
from zana_core.instances.models import StartPlan
from zana_core.runtimes.base import ModelDescriptor, RuntimeDescriptor, RuntimeStatus


class WeakIdentityError(InstanceError):
    """The image does not declare an exact base model digest."""


class RuntimeUnavailableError(InstanceError):
    """No compatible runtime is currently online."""


class RuntimeIncompatibleError(InstanceError):
    """The online runtime is not compatible with the image."""


class ModelIdentityMismatchError(InstanceError):
    """No discovered model matches the exact image model identity."""


class ModelCapabilityMismatchError(InstanceError):
    """The exact model is missing required capabilities."""


class RuntimeSelectionService:
    """Select one exact model/runtime pair; display names are never enough."""

    def select(
        self,
        *,
        config: ZanaImageConfig,
        image_digest: str,
        instance_id: str,
        expected_state_revision: int,
        runtimes: Sequence[RuntimeDescriptor],
        models: Sequence[ModelDescriptor],
    ) -> StartPlan:
        """Produce a start plan or fail closed with a typed error."""
        identity_digest = config.base_model.identity_digest
        if identity_digest is None:
            raise WeakIdentityError(
                "Image base model identity has no exact digest; start is blocked."
            )

        online = [
            runtime
            for runtime in runtimes
            if runtime.status is RuntimeStatus.ONLINE and runtime.server_running
        ]
        if not online:
            raise RuntimeUnavailableError(
                "No compatible runtime is online; refresh discovery and retry."
            )

        if config.base_model.runtime_compatibility:
            compatible_kinds = set(config.base_model.runtime_compatibility)
            online = [runtime for runtime in online if runtime.kind.value in compatible_kinds]
        if not online:
            raise RuntimeIncompatibleError(
                "The online runtimes are not in the image compatibility list."
            )

        exact_models = [
            model
            for model in models
            if model.digest is not None
            and model.digest == identity_digest
            and model.runtime_id in {runtime.runtime_id for runtime in online}
        ]
        if not exact_models:
            raise ModelIdentityMismatchError(
                "No discovered model matches the exact image base digest; "
                "runtime/model drift or disappearance blocks start."
            )

        required = set(config.base_model.required_capabilities)
        if required:
            exact_models = [
                model for model in exact_models if required.issubset(set(model.capabilities))
            ]
            if not exact_models:
                raise ModelCapabilityMismatchError(
                    "The exact base model is missing required capabilities."
                )

        model = exact_models[0]
        runtime = next(runtime for runtime in online if runtime.runtime_id == model.runtime_id)
        assert model.digest is not None
        return StartPlan(
            instance_id=instance_id,
            image_digest=image_digest,
            base_model_digest=identity_digest,
            runtime_id=runtime.runtime_id,
            runtime_endpoint=runtime.endpoint,
            model_key=f"{runtime.runtime_id}:{model.model_id}",
            runtime_model_id=model.model_id,
            model_digest=model.digest,
            expected_state_revision=expected_state_revision,
            required_artifact_digests=(),
            required_secret_references=(),
        )
