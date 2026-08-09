"""Unit-of-work scoping repositories to one SQLite session/transaction."""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from zana_core.db.repositories import (
    ArtifactRepository,
    BuildJobRepository,
    CapabilityRepository,
    CapabilitySourceRepository,
    ConversationRepository,
    ImageArtifactRepository,
    ImageRepository,
    InstanceRepository,
    JobEventRepository,
    JobRepository,
    MemoryRepository,
    MessageRepository,
    ModelRepository,
    RuntimeRepository,
    StateSnapshotRepository,
)


class UnitOfWork:
    """One session with lazily created repositories; commit or roll back as a unit."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session = session_factory()
        self._runtimes: RuntimeRepository | None = None
        self._models: ModelRepository | None = None
        self._capabilities: CapabilityRepository | None = None
        self._capability_sources: CapabilitySourceRepository | None = None
        self._jobs: JobRepository | None = None
        self._job_events: JobEventRepository | None = None
        self._build_jobs: BuildJobRepository | None = None
        self._artifacts: ArtifactRepository | None = None
        self._images: ImageRepository | None = None
        self._image_artifacts: ImageArtifactRepository | None = None
        self._instances: InstanceRepository | None = None
        self._conversations: ConversationRepository | None = None
        self._messages: MessageRepository | None = None
        self._memories: MemoryRepository | None = None
        self._state_snapshots: StateSnapshotRepository | None = None

    @property
    def runtimes(self) -> RuntimeRepository:
        if self._runtimes is None:
            self._runtimes = RuntimeRepository(self.session)
        return self._runtimes

    @property
    def models(self) -> ModelRepository:
        if self._models is None:
            self._models = ModelRepository(self.session)
        return self._models

    @property
    def capabilities(self) -> CapabilityRepository:
        if self._capabilities is None:
            self._capabilities = CapabilityRepository(self.session)
        return self._capabilities

    @property
    def capability_sources(self) -> CapabilitySourceRepository:
        if self._capability_sources is None:
            self._capability_sources = CapabilitySourceRepository(self.session)
        return self._capability_sources

    @property
    def jobs(self) -> JobRepository:
        if self._jobs is None:
            self._jobs = JobRepository(self.session)
        return self._jobs

    @property
    def job_events(self) -> JobEventRepository:
        if self._job_events is None:
            self._job_events = JobEventRepository(self.session)
        return self._job_events

    @property
    def build_jobs(self) -> BuildJobRepository:
        if self._build_jobs is None:
            self._build_jobs = BuildJobRepository(self.session)
        return self._build_jobs

    @property
    def artifacts(self) -> ArtifactRepository:
        if self._artifacts is None:
            self._artifacts = ArtifactRepository(self.session)
        return self._artifacts

    @property
    def images(self) -> ImageRepository:
        if self._images is None:
            self._images = ImageRepository(self.session)
        return self._images

    @property
    def image_artifacts(self) -> ImageArtifactRepository:
        if self._image_artifacts is None:
            self._image_artifacts = ImageArtifactRepository(self.session)
        return self._image_artifacts

    @property
    def instances(self) -> InstanceRepository:
        if self._instances is None:
            self._instances = InstanceRepository(self.session)
        return self._instances

    @property
    def conversations(self) -> ConversationRepository:
        if self._conversations is None:
            self._conversations = ConversationRepository(self.session)
        return self._conversations

    @property
    def messages(self) -> MessageRepository:
        if self._messages is None:
            self._messages = MessageRepository(self.session)
        return self._messages

    @property
    def memories(self) -> MemoryRepository:
        if self._memories is None:
            self._memories = MemoryRepository(self.session)
        return self._memories

    @property
    def state_snapshots(self) -> StateSnapshotRepository:
        if self._state_snapshots is None:
            self._state_snapshots = StateSnapshotRepository(self.session)
        return self._state_snapshots

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> UnitOfWork:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:  # noqa: ANN001
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()
