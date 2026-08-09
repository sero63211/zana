"""Real repository CRUD over the ZANA durable entities."""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from zana_core.db.models import (
    Artifact,
    Base,
    BuildJob,
    Capability,
    CapabilitySource,
    Conversation,
    Image,
    ImageArtifact,
    Instance,
    Job,
    JobEvent,
    Memory,
    Message,
    Model,
    Runtime,
    StateSnapshot,
)
from zana_core.domain.enums import RuntimeSource, RuntimeStatus

EntityT = TypeVar("EntityT", bound=Base)


class RepositoryBase(Generic[EntityT]):
    """Typed CRUD shared by all repositories."""

    model: type[EntityT]

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, entity: EntityT) -> EntityT:
        self.session.add(entity)
        return entity

    def get(self, key: Any) -> EntityT | None:
        return self.session.get(self.model, key)

    def list(self) -> list[EntityT]:
        return list(self.session.scalars(select(self.model)))

    def delete(self, entity: EntityT) -> None:
        self.session.delete(entity)


class RuntimeRepository(RepositoryBase[Runtime]):
    model = Runtime

    def get_by_endpoint(self, endpoint: str, source: RuntimeSource) -> Runtime | None:
        stmt = select(Runtime).where(Runtime.endpoint == endpoint, Runtime.source == source)
        return self.session.scalar(stmt)

    def list_manual(self) -> list[Runtime]:
        stmt = select(Runtime).where(Runtime.source == RuntimeSource.MANUAL)
        return list(self.session.scalars(stmt))


class ModelRepository(RepositoryBase[Model]):
    model = Model

    def list_by_runtime(self, runtime_id: int) -> list[Model]:
        stmt = select(Model).where(Model.runtime_id == runtime_id)
        return list(self.session.scalars(stmt))

    def list_by_capability(self, capability: str) -> list[Model]:
        stmt = select(Model).where(Model.capabilities_json.contains(f'"{capability}"'))
        return list(self.session.scalars(stmt))

    def list_runnable(self) -> list[Model]:
        stmt = select(Model).join(Runtime).where(Runtime.status == RuntimeStatus.ONLINE)
        return list(self.session.scalars(stmt))


class CapabilityRepository(RepositoryBase[Capability]):
    model = Capability

    def list_by_updated_at_desc(self) -> list[Capability]:
        stmt = select(Capability).order_by(Capability.updated_at.desc())
        return list(self.session.scalars(stmt))


class CapabilitySourceRepository(RepositoryBase[CapabilitySource]):
    model = CapabilitySource

    def list_for_capability(self, capability_id: int) -> list[CapabilitySource]:
        stmt = select(CapabilitySource).where(CapabilitySource.capability_id == capability_id)
        return list(self.session.scalars(stmt))


class JobRepository(RepositoryBase[Job]):
    model = Job

    def list_active(self) -> list[Job]:
        stmt = select(Job).where(Job.status.in_(["PENDING", "RUNNING"]))
        return list(self.session.scalars(stmt))


class JobEventRepository(RepositoryBase[JobEvent]):
    model = JobEvent

    def list_for_job(self, job_id: int) -> list[JobEvent]:
        stmt = (
            select(JobEvent)
            .where(JobEvent.job_id == job_id)
            .order_by(JobEvent.created_at, JobEvent.id)
        )
        return list(self.session.scalars(stmt))


class BuildJobRepository(RepositoryBase[BuildJob]):
    model = BuildJob

    def list_for_capability(self, capability_id: int) -> list[BuildJob]:
        stmt = select(BuildJob).where(BuildJob.capability_id == capability_id)
        return list(self.session.scalars(stmt))


class ArtifactRepository(RepositoryBase[Artifact]):
    model = Artifact

    def get_by_role(self, image_digest: str, role: str) -> Artifact | None:
        stmt = (
            select(Artifact)
            .join(ImageArtifact, ImageArtifact.artifact_digest == Artifact.digest)
            .where(ImageArtifact.image_digest == image_digest, ImageArtifact.role == role)
        )
        return self.session.scalar(stmt)


class ImageRepository(RepositoryBase[Image]):
    model = Image


class ImageArtifactRepository(RepositoryBase[ImageArtifact]):
    model = ImageArtifact

    def list_for_image(self, image_digest: str) -> list[ImageArtifact]:
        stmt = select(ImageArtifact).where(ImageArtifact.image_digest == image_digest)
        return list(self.session.scalars(stmt))


class InstanceRepository(RepositoryBase[Instance]):
    model = Instance


class ConversationRepository(RepositoryBase[Conversation]):
    model = Conversation

    def list_for_instance(self, instance_id: int) -> list[Conversation]:
        stmt = select(Conversation).where(Conversation.instance_id == instance_id)
        return list(self.session.scalars(stmt))


class MessageRepository(RepositoryBase[Message]):
    model = Message

    def list_for_conversation(self, conversation_id: int) -> list[Message]:
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
        )
        return list(self.session.scalars(stmt))


class MemoryRepository(RepositoryBase[Memory]):
    model = Memory

    def list_for_instance(self, instance_id: int) -> list[Memory]:
        stmt = select(Memory).where(Memory.instance_id == instance_id)
        return list(self.session.scalars(stmt))


class StateSnapshotRepository(RepositoryBase[StateSnapshot]):
    model = StateSnapshot
