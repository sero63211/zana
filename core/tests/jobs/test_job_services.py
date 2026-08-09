"""Persistent job/event state and cancellation-safe transition tests."""

from __future__ import annotations

import pytest

from zana_core.db.models import Capability, Job, Model, Runtime
from zana_core.db.repositories import JobEventRepository
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


class HostileInt(int):
    def __index__(self) -> int:
        self.calls["index"] = self.calls.get("index", 0) + 1
        return 1

    def __int__(self) -> int:
        self.calls["int"] = self.calls.get("int", 0) + 1
        return 1

    def __eq__(self, other: object) -> bool:
        self.calls["eq"] = self.calls.get("eq", 0) + 1
        return super().__eq__(other)


class HostileFloat(float):
    def __float__(self) -> float:
        self.calls["float"] = self.calls.get("float", 0) + 1
        return 1.0

    def __lt__(self, other: object) -> bool:
        self.calls["lt"] = self.calls.get("lt", 0) + 1
        return super().__lt__(other)

    def __gt__(self, other: object) -> bool:
        self.calls["gt"] = self.calls.get("gt", 0) + 1
        return super().__gt__(other)

    def __eq__(self, other: object) -> bool:
        self.calls["eq"] = self.calls.get("eq", 0) + 1
        return super().__eq__(other)


class HookObject:
    def __init__(self) -> None:
        self.calls: dict[str, int] = {}

    def __index__(self) -> int:
        self.calls["index"] = self.calls.get("index", 0) + 1
        return 1

    def __int__(self) -> int:
        self.calls["int"] = self.calls.get("int", 0) + 1
        return 1

    def __hash__(self) -> int:
        self.calls["hash"] = self.calls.get("hash", 0) + 1
        return 1

    def __eq__(self, other: object) -> bool:
        self.calls["eq"] = self.calls.get("eq", 0) + 1
        return True

    def __float__(self) -> float:
        self.calls["float"] = self.calls.get("float", 0) + 1
        return 1.0

    def __lt__(self, other: object) -> bool:
        self.calls["lt"] = self.calls.get("lt", 0) + 1
        return True

    def __gt__(self, other: object) -> bool:
        self.calls["gt"] = self.calls.get("gt", 0) + 1
        return True


def _hostile_int(value: int) -> HostileInt:
    result = HostileInt(value)
    result.calls = {}
    return result


def _hostile_float(value: float) -> HostileFloat:
    result = HostileFloat(value)
    result.calls = {}
    return result


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

    def test_list_events_reads_bounded_page_after_event_id(
        self,
        uow: UnitOfWork,
    ) -> None:
        service = JobService(uow)
        job = service.create_job(JobKind.RUNTIME_REFRESH)
        service.transition_job(job.id, JobStatus.RUNNING)
        service.record_progress(job.id, 0.5, phase="working")
        uow.commit()

        page = service.list_events(job.id, after_event_id=1, limit=1)
        assert len(page) == 1
        assert page[0].id == 2
        assert page[0].kind == JobEventKind.STATUS_CHANGED

        next_page = service.list_events(job.id, after_event_id=2, limit=10)
        assert len(next_page) == 1
        assert next_page[0].kind == JobEventKind.PROGRESS

    def test_list_events_rejects_non_positive_limit(
        self,
        uow: UnitOfWork,
    ) -> None:
        service = JobService(uow)
        job = service.create_job(JobKind.MODEL_PULL)
        uow.commit()
        with pytest.raises(ValueError):
            service.list_events(job.id, limit=0)

    def test_list_events_rejects_limit_above_server_cap(
        self,
        uow: UnitOfWork,
    ) -> None:
        service = JobService(uow)
        job = service.create_job(JobKind.MODEL_PULL)
        uow.commit()
        with pytest.raises(ValueError, match="between 1 and 100"):
            service.list_events(job.id, limit=101)

    def test_list_events_rejects_negative_cursor(
        self,
        uow: UnitOfWork,
    ) -> None:
        service = JobService(uow)
        job = service.create_job(JobKind.MODEL_PULL)
        uow.commit()
        with pytest.raises(ValueError, match="non-negative"):
            service.list_events(job.id, after_event_id=-1)

    def test_list_events_never_exceeds_requested_limit(
        self,
        uow: UnitOfWork,
    ) -> None:
        service = JobService(uow)
        job = service.create_job(JobKind.BUILD_ANALYSIS)
        for index in range(5):
            service.record_progress(job.id, (index + 1) / 5, phase="x")
        uow.commit()
        assert len(service.list_events(job.id, limit=2)) == 2

    def test_repository_rejects_limit_above_server_cap(
        self,
        uow: UnitOfWork,
    ) -> None:
        with pytest.raises(ValueError, match="between 1 and 100"):
            JobEventRepository(uow.session).list_for_job(1, limit=101)

    def test_repository_rejects_negative_cursor(
        self,
        uow: UnitOfWork,
    ) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            JobEventRepository(uow.session).list_for_job(1, after_event_id=-1)


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


class TestExactTypeGates:
    def test_service_rejects_int_subclasses_before_any_hook(
        self,
        uow: UnitOfWork,
    ) -> None:
        service = JobService(uow)
        job_id = _hostile_int(1)
        after_event_id = _hostile_int(0)
        limit = _hostile_int(50)
        with pytest.raises(TypeError):
            service.list_events(job_id)
        with pytest.raises(TypeError):
            service.list_events(1, after_event_id=after_event_id)
        with pytest.raises(TypeError):
            service.list_events(1, limit=limit)
        with pytest.raises(TypeError):
            service.get_job(job_id)
        with pytest.raises(TypeError):
            service.get_job(True)
        assert job_id.calls == {}
        assert after_event_id.calls == {}
        assert limit.calls == {}

    def test_repository_rejects_int_subclasses_before_sql(
        self,
        uow: UnitOfWork,
    ) -> None:
        repository = JobEventRepository(uow.session)
        job_id = _hostile_int(1)
        after_event_id = _hostile_int(0)
        limit = _hostile_int(50)
        with pytest.raises(TypeError):
            repository.list_for_job(job_id)
        with pytest.raises(TypeError):
            repository.list_for_job(1, after_event_id=after_event_id)
        with pytest.raises(TypeError):
            repository.list_for_job_stream(1, limit=limit)
        assert job_id.calls == {}
        assert after_event_id.calls == {}
        assert limit.calls == {}

    def test_service_and_repository_reject_hook_objects(self, uow: UnitOfWork) -> None:
        service = JobService(uow)
        repository = JobEventRepository(uow.session)

        hook = HookObject()
        with pytest.raises(TypeError):
            service.list_events(hook)
        assert hook.calls == {}

        hook = HookObject()
        with pytest.raises(TypeError):
            repository.list_for_job(hook)
        assert hook.calls == {}


class TestProgressExactTypeGates:
    def test_transition_job_rejects_hostile_progress_without_hooks(
        self,
        uow: UnitOfWork,
    ) -> None:
        service = JobService(uow)
        for value in (_hostile_int(1), _hostile_float(0.5), HookObject()):
            job = service.create_job(JobKind.BUILD)
            with pytest.raises(TypeError):
                service.transition_job(
                    job.id,
                    JobStatus.RUNNING,
                    progress_0_1=value,  # type: ignore[arg-type]
                )
            assert value.calls == {}
        for value in (True, False):
            job = service.create_job(JobKind.BUILD)
            with pytest.raises(TypeError):
                service.transition_job(
                    job.id,
                    JobStatus.RUNNING,
                    progress_0_1=value,  # type: ignore[arg-type]
                )

    def test_record_progress_rejects_hostile_progress_without_hooks(
        self,
        uow: UnitOfWork,
    ) -> None:
        service = JobService(uow)
        job = service.create_job(JobKind.BUILD)
        for value in (
            _hostile_int(1),
            _hostile_float(0.5),
            HookObject(),
        ):
            with pytest.raises(TypeError):
                service.record_progress(
                    job.id,
                    value,  # type: ignore[arg-type]
                    phase="working",
                )
            assert value.calls == {}
        assert uow.jobs.get(job.id).progress_0_1 == 0.0

    def test_progress_rejects_non_finite_through_public_paths(
        self,
        uow: UnitOfWork,
    ) -> None:
        service = JobService(uow)
        job = service.create_job(JobKind.BUILD)
        for value in (float("nan"), float("inf"), float("-inf")):
            with pytest.raises(ValueError):
                service.transition_job(
                    job.id,
                    JobStatus.RUNNING,
                    progress_0_1=value,
                )
            with pytest.raises(ValueError):
                service.record_progress(job.id, value, phase="working")
        assert uow.jobs.get(job.id).progress_0_1 == 0.0

    def test_progress_clamps_finite_exact_values(
        self,
        uow: UnitOfWork,
    ) -> None:
        service = JobService(uow)
        job = service.create_job(JobKind.BUILD)
        service.transition_job(job.id, JobStatus.RUNNING, progress_0_1=1.5)
        assert uow.jobs.get(job.id).progress_0_1 == 1.0
        service.record_progress(job.id, -1, phase="working")
        assert uow.jobs.get(job.id).progress_0_1 == 0.0
        service.record_progress(job.id, 2, phase="working")
        assert uow.jobs.get(job.id).progress_0_1 == 1.0
        service.record_progress(job.id, 0.5, phase="working")
        assert uow.jobs.get(job.id).progress_0_1 == 0.5
