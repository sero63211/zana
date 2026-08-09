"""Persistent job/event state and cancellation-safe transition tests."""

from __future__ import annotations

import pytest

from zana_core.db.models import Capability, Job, Model, Runtime
from zana_core.db.unit_of_work import UnitOfWork
from zana_core.domain.enums import (
    BuildJobStatus,
    JobEventKind,
    JobKind,
    JobStatus,
    ModelIdentityStrength,
    RuntimeKind,
    RuntimeSource,
    RuntimeStatus,
)
from zana_core.jobs.services import BuildJobService, JobService
from zana_core.jobs.state_machine import InvalidJobTransitionError


class TestJobService:
    def test_create_job_persists_created_event(self, uow: UnitOfWork) -> None:
        service = JobService(uow)
        job = service.create_job(JobKind.MODEL_PULL, phase="queue", message="waiting")
        uow.commit()

        assert job.id is not None
        assert job.status == JobStatus.PENDING
        events = service.list_events(job.id)
        assert len(events) == 1
        assert events[0].kind == JobEventKind.CREATED
        assert events[0].job_id == job.id

    def test_transitions_persist_status_and_events(self, uow: UnitOfWork) -> None:
        service = JobService(uow)
        job = service.create_job(JobKind.RUNTIME_REFRESH)
        service.transition_job(
            job.id,
            JobStatus.RUNNING,
            phase="probing",
            progress_0_1=0.25,
        )
        service.record_progress(job.id, 0.5, phase="probing", message="halfway")
        service.transition_job(job.id, JobStatus.SUCCEEDED, phase="done")
        uow.commit()

        persisted = uow.jobs.get(job.id)
        assert isinstance(persisted, Job)
        assert persisted.status == JobStatus.SUCCEEDED
        assert persisted.phase == "done"
        assert persisted.progress_0_1 == 0.5
        kinds = [event.kind for event in service.list_events(job.id)]
        assert kinds == [
            JobEventKind.CREATED,
            JobEventKind.STATUS_CHANGED,
            JobEventKind.PROGRESS,
            JobEventKind.STATUS_CHANGED,
        ]

    def test_cancel_is_safe_and_terminal_cancel_is_rejected(self, uow: UnitOfWork) -> None:
        service = JobService(uow)
        job = service.create_job(JobKind.BUILD_ANALYSIS)
        service.transition_job(job.id, JobStatus.RUNNING)
        service.cancel_job(job.id, reason="user cancelled")
        uow.commit()

        assert uow.jobs.get(job.id).status == JobStatus.CANCELLED
        assert any(e.kind == JobEventKind.CANCELLED for e in service.list_events(job.id))
        with pytest.raises(InvalidJobTransitionError):
            service.cancel_job(job.id)

    def test_invalid_transition_is_rejected(self, uow: UnitOfWork) -> None:
        service = JobService(uow)
        job = service.create_job(JobKind.MODEL_PULL)
        service.transition_job(job.id, JobStatus.RUNNING)
        uow.commit()
        with pytest.raises(InvalidJobTransitionError):
            service.transition_job(job.id, JobStatus.PENDING)
        assert uow.jobs.get(job.id).status == JobStatus.RUNNING


class TestBuildJobService:
    @staticmethod
    def _seed_capability_and_model(uow: UnitOfWork) -> tuple[int, str]:
        runtime = uow.runtimes.add(
            Runtime(
                kind=RuntimeKind.OLLAMA,
                endpoint="http://127.0.0.1:11434",
                source=RuntimeSource.AUTO,
                status=RuntimeStatus.ONLINE,
            )
        )
        uow.session.flush()
        model = uow.models.add(
            Model(
                key="ollama:example",
                runtime_id=runtime.id,
                model_id="example",
                capabilities_json=["completion"],
                identity_strength=ModelIdentityStrength.EXACT_DIGEST,
            )
        )
        capability = uow.capabilities.add(Capability(name="math-tutor", version="0.1.0"))
        uow.session.flush()
        return capability.id, model.key

    def test_analysis_job_starts_in_draft_and_can_be_cancelled(
        self,
        uow: UnitOfWork,
    ) -> None:
        service = BuildJobService(uow)
        capability_id, model_key = self._seed_capability_and_model(uow)
        build_job = service.create_analysis_job(
            capability_id=capability_id,
            model_key=model_key,
            policy={"strategy": "RAG_ONLY"},
        )
        uow.commit()

        assert build_job.id is not None
        assert build_job.status == BuildJobStatus.DRAFT
        assert build_job.error_json["code"] == "ANALYSIS_NOT_STARTED"
        cancelled = service.cancel_build_job(build_job.id)
        uow.commit()
        assert cancelled.status == BuildJobStatus.CANCELLED
        assert cancelled.completed_at is not None

    def test_build_transition_and_terminal_cancel_rejected(self, uow: UnitOfWork) -> None:
        service = BuildJobService(uow)
        capability_id, model_key = self._seed_capability_and_model(uow)
        build_job = service.create_analysis_job(
            capability_id=capability_id,
            model_key=model_key,
            policy={},
        )
        service.transition_build_job(build_job.id, BuildJobStatus.ANALYZING)
        service.transition_build_job(build_job.id, BuildJobStatus.BLOCKED)
        uow.commit()
        assert uow.build_jobs.get(build_job.id).status == BuildJobStatus.BLOCKED
        with pytest.raises(InvalidJobTransitionError):
            service.cancel_build_job(build_job.id)
