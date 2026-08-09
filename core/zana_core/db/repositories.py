"""Real repository CRUD over the ZANA durable entities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generic, TypeVar

from sqlalchemy import BLOB, String, case, func, select, text
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


@dataclass(frozen=True)
class JobEventStreamRow:
    """Bounded SQL projection for SSE; oversized text/JSON never enters Python."""

    id: int
    job_id: int
    kind: str
    phase: str
    message: str
    progress_0_1: float
    error_json: str | None
    created_at: datetime


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

    MAX_EVENT_PAGE_SIZE = 100
    STREAM_MAX_MESSAGE_CHARS = 256
    STREAM_MAX_PHASE_CHARS = 24
    STREAM_MAX_KIND_CHARS = 32
    STREAM_MAX_ERROR_BYTES = 1024

    @staticmethod
    def _require_positive_int(value: object, name: str, maximum: int) -> int:
        if type(value) is not int:
            raise TypeError(f"{name} must be an int")
        if value <= 0 or value > maximum:
            raise ValueError(f"{name} must be between 1 and {maximum}")
        return value

    @staticmethod
    def _require_non_negative_int(value: object, name: str) -> int:
        if type(value) is not int:
            raise TypeError(f"{name} must be an int")
        if value < 0:
            raise ValueError(f"{name} must be non-negative")
        return value

    def list_for_job_stream(
        self,
        job_id: int,
        *,
        after_event_id: int = 0,
        limit: int = 50,
    ) -> list[JobEventStreamRow]:
        """SQL-side bounded projection for the SSE endpoint.

        Message/phase are capped with SQLite ``substr`` before Python sees
        them. ``error_json`` is never deserialized: an oversized or invalid
        JSON value is projected as a small typed sentinel string, and normal
        values are returned as the raw bounded string. ``LIMIT`` is always
        enforced and ordering is by stable ascending id.
        """
        limit = self._require_positive_int(limit, "limit", self.MAX_EVENT_PAGE_SIZE)
        after_event_id = self._require_non_negative_int(after_event_id, "after_event_id")
        job_id = self._require_non_negative_int(job_id, "job_id")

        message_prefix = func.substr(JobEvent.message, 1, self.STREAM_MAX_MESSAGE_CHARS)
        phase_prefix = func.substr(JobEvent.phase, 1, self.STREAM_MAX_PHASE_CHARS)
        error_sentinel = text('\'{"code":"REDACTED_ERROR","message":"[truncated]"}\'')
        error_projection = case(
            (
                JobEvent.error_json.is_(None),
                None,
            ),
            (
                func.length(func.cast(JobEvent.error_json, BLOB)) > self.STREAM_MAX_ERROR_BYTES,
                error_sentinel,
            ),
            else_=func.cast(JobEvent.error_json, String),
        )
        kind_prefix = func.substr(JobEvent.kind, 1, self.STREAM_MAX_KIND_CHARS)

        stmt = (
            select(
                JobEvent.id,
                JobEvent.job_id,
                func.cast(kind_prefix, String).label("kind"),
                phase_prefix.label("phase"),
                message_prefix.label("message"),
                JobEvent.progress_0_1,
                error_projection.label("error_json"),
                JobEvent.created_at,
            )
            .where(
                JobEvent.job_id == job_id,
                JobEvent.id > after_event_id,
            )
            .order_by(JobEvent.id)
            .limit(limit)
        )
        rows = self.session.execute(stmt).all()
        return [
            JobEventStreamRow(
                id=row.id,
                job_id=row.job_id,
                kind=row.kind,
                phase=row.phase,
                message=row.message,
                progress_0_1=row.progress_0_1,
                error_json=row.error_json,
                created_at=row.created_at,
            )
            for row in rows
        ]

    def list_for_job(
        self,
        job_id: int,
        *,
        after_event_id: int = 0,
        limit: int = 50,
    ) -> list[JobEvent]:
        """Read one bounded ascending page of events for an exact job.

        Always uses SQL ``LIMIT`` and never loads all rows. The default page
        is conservative and callers must keep the value below the server
        maximum.
        """
        limit = self._require_positive_int(limit, "limit", self.MAX_EVENT_PAGE_SIZE)
        after_event_id = self._require_non_negative_int(after_event_id, "after_event_id")
        job_id = self._require_non_negative_int(job_id, "job_id")
        stmt = (
            select(JobEvent)
            .where(
                JobEvent.job_id == job_id,
                JobEvent.id > after_event_id,
            )
            .order_by(JobEvent.id)
            .limit(limit)
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
