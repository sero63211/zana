"""Instance creation preconditions and immutable/mutable binding tests."""

from __future__ import annotations

import pytest

from tests.instances.helpers import (
    ADAPTER_DIGEST,
    BEHAVIOR_DIGEST,
    IDENTITY_DIGEST,
    IMAGE_DIGEST,
    KNOWLEDGE_DIGEST,
    PERMISSIONS_DIGEST,
    create_instance,
    declared_artifact_set,
    make_image_config,
    make_model,
)
from zana_core.domain.enums import InstanceStatus
from zana_core.instances.creation import (
    InstanceCreationService,
    NotRunnableImageError,
    UnresolvedArtifactError,
    UnresolvedSecretError,
)


class TestCreationPreconditions:
    def test_missing_base_digest_blocks_creation(self) -> None:
        config = make_image_config(identity_digest=None)
        with pytest.raises(NotRunnableImageError, match="weak|runnable"):
            create_instance(config=config)

    def test_not_runnable_image_blocks_creation(self) -> None:
        config = make_image_config(identity_digest="sha256:" + "9" * 64)
        with pytest.raises(NotRunnableImageError):
            create_instance(config=config)

    def test_unresolved_artifact_blocks_creation(self) -> None:
        config = make_image_config()
        artifacts = declared_artifact_set() - {KNOWLEDGE_DIGEST}
        with pytest.raises(UnresolvedArtifactError, match="unresolved"):
            create_instance(config=config, artifacts=artifacts)

    def test_unresolved_secret_blocks_creation(self) -> None:
        config = make_image_config(secrets_allow=("runtime.api_token",))
        with pytest.raises(UnresolvedSecretError, match="unresolved"):
            create_instance(config=config, secrets=set())

    def test_resolved_secret_allows_creation(self) -> None:
        config = make_image_config(secrets_allow=("runtime.api_token",))
        instance = create_instance(
            config=config,
            secrets={"runtime.api_token"},
        )
        assert "runtime.api_token" in instance.config.required_secret_references


class TestCreationBinding:
    def test_create_binds_image_config_and_mutable_state_separately(self) -> None:
        config = make_image_config(tool_ids=("zana.calculator",))
        instance = create_instance(config=config)
        assert instance.status is InstanceStatus.STOPPED
        assert instance.config.image_digest == IMAGE_DIGEST
        assert instance.config.base_model_digest == IDENTITY_DIGEST
        assert instance.pointer.image.digest == IMAGE_DIGEST
        assert instance.pointer.snapshot_revision == 0
        assert instance.state.instance_id == instance.instance_id
        assert instance.state.state_revision == 0
        assert instance.config.knowledge_snapshot_digest == KNOWLEDGE_DIGEST
        assert instance.config.tool_ids == ("zana.calculator",)
        assert BEHAVIOR_DIGEST in instance.config.required_artifact_digests
        assert ADAPTER_DIGEST in instance.config.required_artifact_digests
        assert PERMISSIONS_DIGEST in instance.config.required_artifact_digests

    def test_create_uses_supplied_instance_id(self) -> None:
        instance = create_instance()
        service = InstanceCreationService()
        other = service.create(
            config=make_image_config(),
            image_digest=IMAGE_DIGEST,
            runtimes=[],
            models=[make_model()],
            available_artifacts=declared_artifact_set(),
            resolved_secrets=set(),
            instance_id="explicit-id",
        )
        assert other.instance_id == "explicit-id"
        assert instance.instance_id != other.instance_id
