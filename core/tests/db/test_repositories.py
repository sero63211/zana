"""Real repository and unit-of-work CRUD tests."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from zana_core.db.models import (
    Artifact,
    BuildJob,
    Capability,
    CapabilitySource,
    Conversation,
    Image,
    ImageArtifact,
    Instance,
    Memory,
    Message,
    Model,
    Runtime,
    StateSnapshot,
)
from zana_core.db.unit_of_work import UnitOfWork
from zana_core.domain.enums import (
    BuildJobStatus,
    InstanceStatus,
    MemoryStatus,
    MessageRole,
    ModelIdentityStrength,
    RuntimeKind,
    RuntimeSource,
    RuntimeStatus,
    VerificationStatus,
)


class TestRuntimeAndModelRepositories:
    def test_runtime_and_model_crud_roundtrip(self, uow: UnitOfWork) -> None:
        runtime = uow.runtimes.add(
            Runtime(
                kind=RuntimeKind.OLLAMA,
                endpoint="http://127.0.0.1:11434",
                source=RuntimeSource.MANUAL,
                status=RuntimeStatus.UNKNOWN,
                metadata_json={"probe": "pending"},
            )
        )
        uow.commit()

        assert runtime.id is not None
        assert uow.runtimes.get(runtime.id) is not None
        endpoint = "http://127.0.0.1:11434"
        assert uow.runtimes.get_by_endpoint(endpoint, RuntimeSource.MANUAL) is not None

        model = uow.models.add(
            Model(
                key="ollama:example-model",
                runtime_id=runtime.id,
                model_id="example-model",
                digest="sha256:abc",
                capabilities_json=["completion"],
                identity_strength=ModelIdentityStrength.EXACT_DIGEST,
                metadata_json={"source": "probe"},
            )
        )
        uow.commit()

        assert uow.models.get(model.key) is not None
        assert uow.models.list_by_runtime(runtime.id) == [model]
        assert uow.models.list_by_capability("completion") == [model]
        assert uow.models.list() == [model]

    def test_runtime_identity_includes_kind_for_shared_endpoint(self, uow: UnitOfWork) -> None:
        endpoint = "http://127.0.0.1:8080"
        llama = uow.runtimes.add(
            Runtime(
                kind=RuntimeKind.LLAMA_CPP,
                endpoint=endpoint,
                source=RuntimeSource.AUTO,
                status=RuntimeStatus.UNKNOWN,
            )
        )
        mlx = uow.runtimes.add(
            Runtime(
                kind=RuntimeKind.MLX_LM,
                endpoint=endpoint,
                source=RuntimeSource.AUTO,
                status=RuntimeStatus.UNKNOWN,
            )
        )
        uow.commit()

        found_llama = uow.runtimes.get_by_kind_endpoint(
            RuntimeKind.LLAMA_CPP, endpoint, RuntimeSource.AUTO
        )
        found_mlx = uow.runtimes.get_by_kind_endpoint(
            RuntimeKind.MLX_LM, endpoint, RuntimeSource.AUTO
        )
        assert found_llama is llama
        assert found_mlx is mlx

    def test_runtime_identity_query_is_bounded(self, uow: UnitOfWork) -> None:
        with pytest.raises(TypeError):
            uow.runtimes.get_by_kind_endpoint("ollama", "http://127.0.0.1:1", RuntimeSource.AUTO)
        with pytest.raises(ValueError):
            uow.runtimes.get_by_kind_endpoint(RuntimeKind.OLLAMA, "x" * 2001, RuntimeSource.AUTO)

    def test_foreign_key_prevents_orphan_model(self, uow: UnitOfWork) -> None:
        uow.models.add(
            Model(
                key="orphan:model",
                runtime_id=999_999,
                model_id="model",
                capabilities_json=[],
                identity_strength=ModelIdentityStrength.UNKNOWN,
            )
        )
        with pytest.raises(IntegrityError):
            uow.commit()

    def test_rollback_does_not_persist(self, uow: UnitOfWork) -> None:
        uow.runtimes.add(
            Runtime(
                kind=RuntimeKind.UNKNOWN,
                endpoint="http://127.0.0.1:1",
                source=RuntimeSource.MANUAL,
                status=RuntimeStatus.UNKNOWN,
            )
        )
        uow.rollback()
        assert uow.runtimes.list() == []


class TestCapabilityRepositories:
    def test_capability_and_sources_roundtrip(self, uow: UnitOfWork) -> None:
        capability = uow.capabilities.add(
            Capability(
                name="math-tutor",
                version="0.1.0",
                manifest_json={"schemaVersion": 1, "kind": "ZanaCapability"},
            )
        )
        uow.commit()

        source = uow.capability_sources.add(
            CapabilitySource(
                capability_id=capability.id,
                original_name="behavior.md",
                local_path="/data/capabilities/math-tutor/behavior.md",
                sha256="sha256:deadbeef",
                media_type="text/markdown",
                size_bytes=2048,
                metadata_json={"license": "MIT"},
            )
        )
        uow.commit()

        assert source.id is not None
        assert uow.capabilities.get(capability.id) is not None
        assert uow.capability_sources.list_for_capability(capability.id) == [source]
        capability.manifest_json["goal"] = {"type": "domain-assistant"}
        uow.commit()
        persisted = uow.capabilities.get(capability.id)
        assert persisted.manifest_json["goal"] == {"type": "domain-assistant"}


class TestImageAndInstanceRepositories:
    def test_image_artifact_instance_and_state_roundtrip(self, uow: UnitOfWork) -> None:
        artifact = uow.artifacts.add(
            Artifact(
                digest="sha256:artifact",
                media_type="application/vnd.zana.behavior.v1+json",
                local_path="/data/artifacts/sha256/artifact",
                size_bytes=512,
                reference_count=1,
            )
        )
        image = uow.images.add(
            Image(
                digest="sha256:image",
                name="math-tutor",
                version="1.0.0",
                config_digest="sha256:config",
                verification_status=VerificationStatus.UNVERIFIED,
                base_model_key="ollama:example-model",
                base_model_digest="sha256:base",
            )
        )
        uow.image_artifacts.add(
            ImageArtifact(
                image_digest=image.digest,
                artifact_digest=artifact.digest,
                role="behavior",
            )
        )
        instance = uow.instances.add(
            Instance(
                name="math-tutor-1",
                image_digest=image.digest,
                status=InstanceStatus.STOPPED,
            )
        )
        uow.commit()

        conversation = uow.conversations.add(
            Conversation(instance_id=instance.id, title="Homework help")
        )
        uow.commit()
        message = uow.messages.add(
            Message(
                conversation_id=conversation.id,
                role=MessageRole.ASSISTANT,
                content="391.",
                provenance_json={"evidence": ["source.md:12"]},
            )
        )
        memory = uow.memories.add(
            Memory(
                instance_id=instance.id,
                type="fact",
                content="User prefers concise arithmetic answers.",
                source_message_id=message.id,
                status=MemoryStatus.PENDING,
            )
        )
        snapshot = uow.state_snapshots.add(
            StateSnapshot(
                instance_id=instance.id,
                image_digest=image.digest,
                state_digest="sha256:state",
                local_path="/data/instances/1/snapshots/state",
            )
        )
        uow.commit()

        assert uow.images.get(image.digest) is not None
        assert uow.image_artifacts.list_for_image(image.digest)[0].role == "behavior"
        assert uow.artifacts.get_by_role(image.digest, "behavior").digest == artifact.digest
        assert uow.instances.get(instance.id) is not None
        assert uow.conversations.list_for_instance(instance.id) == [conversation]
        assert uow.messages.list_for_conversation(conversation.id) == [message]
        assert uow.memories.list_for_instance(instance.id) == [memory]
        assert uow.state_snapshots.list() == [snapshot]


class TestBuildJobRepository:
    def test_build_job_persistence(self, uow: UnitOfWork) -> None:
        runtime = uow.runtimes.add(
            Runtime(
                kind=RuntimeKind.OPENAI_COMPATIBLE,
                endpoint="http://127.0.0.1:8080",
                source=RuntimeSource.MANUAL,
                status=RuntimeStatus.UNKNOWN,
            )
        )
        uow.session.flush()
        model = uow.models.add(
            Model(
                key="openai:model",
                runtime_id=runtime.id,
                model_id="model",
                capabilities_json=["completion"],
                identity_strength=ModelIdentityStrength.DISPLAY_NAME_ONLY,
            )
        )
        capability = uow.capabilities.add(Capability(name="tutor", version="0.1.0"))
        uow.commit()

        build_job = uow.build_jobs.add(
            BuildJob(
                capability_id=capability.id,
                model_key=model.key,
                status=BuildJobStatus.DRAFT,
                policy_json={"strategy": "RAG_ONLY"},
            )
        )
        uow.commit()

        assert build_job.id is not None
        assert uow.build_jobs.get(build_job.id) is not None
        assert uow.build_jobs.list_for_capability(capability.id) == [build_job]
