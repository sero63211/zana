"""Job and build lifecycle services with persistent event records."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from zana_core.db.models import BuildJob, Job, JobEvent
from zana_core.db.repositories import JobEventStreamRow
from zana_core.db.unit_of_work import UnitOfWork
from zana_core.domain.enums import (
    BuildJobStatus,
    JobEventKind,
    JobKind,
    JobStatus,
)
from zana_core.jobs.state_machine import (
    TERMINAL_BUILD_STATES,
    TERMINAL_JOB_STATES,
    InvalidJobTransitionError,
    require_transition_build,
    require_transition_job,
)


class JobNotFoundError(KeyError):
    """Raised when a job id does not exist."""


MAX_EVENT_PAGE_SIZE = 100


class BuildJobNotFoundError(KeyError):
    """Raised when a build job id does not exist."""


class JobService:
    """Persistent generic job lifecycle."""

    def __init__(self, uow: UnitOfWork) -> None:
        self.uow = uow

    def create_job(
        self,
        kind: JobKind,
        *,
        phase: str = "",
        message: str = "",
    ) -> Job:
        job = Job(kind=kind, status=JobStatus.PENDING, phase=phase, message=message)
        self.uow.jobs.add(job)
        self.uow.session.flush()
        self.uow.job_events.add(
            JobEvent(
                job_id=job.id,
                kind=JobEventKind.CREATED,
                phase=phase,
                message=message,
            )
        )
        return job

    def get_job(self, job_id: int) -> Job:
        _require_non_negative_int(job_id, "job_id")
        job = self.uow.jobs.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        return job

    def transition_job(
        self,
        job_id: int,
        target: JobStatus,
        *,
        phase: str | None = None,
        message: str | None = None,
        progress_0_1: float | None = None,
        error: dict[str, Any] | None = None,
    ) -> Job:
        job = self.get_job(job_id)
        require_transition_job(job.status, target)
        job.status = target
        if phase is not None:
            job.phase = phase
        if message is not None:
            job.message = message
        if progress_0_1 is not None:
            job.progress_0_1 = _clamp_progress(progress_0_1)
        if error is not None:
            job.error_json = error
        self.uow.job_events.add(
            JobEvent(
                job_id=job.id,
                kind=JobEventKind.ERROR if error is not None else JobEventKind.STATUS_CHANGED,
                phase=job.phase,
                message=message or "",
                progress_0_1=job.progress_0_1,
                error_json=error,
            )
        )
        return job

    def cancel_job(self, job_id: int, *, reason: str = "") -> Job:
        job = self.get_job(job_id)
        if job.status in TERMINAL_JOB_STATES:
            raise InvalidJobTransitionError(
                f"Cannot cancel a job in terminal state {job.status.value}."
            )
        job.status = JobStatus.CANCELLED
        if reason:
            job.message = reason
        self.uow.job_events.add(
            JobEvent(
                job_id=job.id,
                kind=JobEventKind.CANCELLED,
                phase=job.phase,
                message=reason,
            )
        )
        return job

    def record_progress(
        self,
        job_id: int,
        progress_0_1: float,
        *,
        phase: str,
        message: str = "",
    ) -> Job:
        job = self.get_job(job_id)
        if job.status in TERMINAL_JOB_STATES:
            raise InvalidJobTransitionError(
                f"Cannot record progress for terminal job {job.status.value}."
            )
        job.progress_0_1 = _clamp_progress(progress_0_1)
        job.phase = phase
        if message:
            job.message = message
        self.uow.job_events.add(
            JobEvent(
                job_id=job.id,
                kind=JobEventKind.PROGRESS,
                phase=phase,
                message=message,
                progress_0_1=job.progress_0_1,
            )
        )
        return job

    def list_events(
        self,
        job_id: int,
        *,
        after_event_id: int = 0,
        limit: int = 50,
    ) -> list[JobEvent]:
        """Read one bounded ascending page of events for an exact job."""
        limit = _require_positive_int(limit, "limit")
        after_event_id = _require_non_negative_int(after_event_id, "after_event_id")
        _require_non_negative_int(job_id, "job_id")
        self.get_job(job_id)
        return self.uow.job_events.list_for_job(
            job_id,
            after_event_id=after_event_id,
            limit=limit,
        )

    def list_event_stream_rows(
        self,
        job_id: int,
        *,
        after_event_id: int = 0,
        limit: int = 50,
    ) -> list[JobEventStreamRow]:
        """Read a SQL-side bounded SSE projection for an exact job."""
        limit = _require_positive_int(limit, "limit")
        after_event_id = _require_non_negative_int(after_event_id, "after_event_id")
        _require_non_negative_int(job_id, "job_id")
        self.get_job(job_id)
        return self.uow.job_events.list_for_job_stream(
            job_id,
            after_event_id=after_event_id,
            limit=limit,
        )


def _require_positive_int(value: object, name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an int")
    if value <= 0 or value > MAX_EVENT_PAGE_SIZE:
        raise ValueError(f"{name} must be between 1 and {MAX_EVENT_PAGE_SIZE}")
    return value


def _require_non_negative_int(value: object, name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an int")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


class BuildJobService:
    """Persistent build lifecycle with cancellation-safe transitions."""

    def __init__(self, uow: UnitOfWork) -> None:
        self.uow = uow

    def create_analysis_job(
        self,
        *,
        capability_id: int,
        model_key: str,
        policy: dict[str, Any],
    ) -> BuildJob:
        _require_non_negative_int(capability_id, "capability_id")
        build_job = BuildJob(
            capability_id=capability_id,
            model_key=model_key,
            status=BuildJobStatus.DRAFT,
            policy_json=policy,
            error_json={
                "code": "ANALYSIS_NOT_STARTED",
                "message": (
                    "The analysis engine is not implemented yet; "
                    "the job was recorded but not started."
                ),
            },
        )
        self.uow.build_jobs.add(build_job)
        self.uow.session.flush()
        return build_job

    def get_build_job(self, build_job_id: int) -> BuildJob:
        _require_non_negative_int(build_job_id, "build_job_id")
        build_job = self.uow.build_jobs.get(build_job_id)
        if build_job is None:
            raise BuildJobNotFoundError(build_job_id)
        return build_job

    def cancel_build_job(self, build_job_id: int) -> BuildJob:
        build_job = self.get_build_job(build_job_id)
        if build_job.status in TERMINAL_BUILD_STATES:
            raise InvalidJobTransitionError(
                f"Cannot cancel a build in terminal state {build_job.status.value}."
            )
        require_transition_build(build_job.status, BuildJobStatus.CANCELLED)
        build_job.status = BuildJobStatus.CANCELLED
        build_job.completed_at = datetime.now(UTC)
        return build_job

    def transition_build_job(
        self,
        build_job_id: int,
        target: BuildJobStatus,
    ) -> BuildJob:
        build_job = self.get_build_job(build_job_id)
        require_transition_build(build_job.status, target)
        build_job.status = target
        if target in TERMINAL_BUILD_STATES:
            build_job.completed_at = datetime.now(UTC)
        return build_job


def _clamp_progress(value: float) -> float:
    """Clamp an exact finite int/float into [0,1] without numeric hooks."""
    if type(value) is int:
        if value <= 0:
            return 0.0
        if value >= 1:
            return 1.0
        return float(value)
    if type(value) is not float:
        raise TypeError("progress_0_1 must be an exact int or float")
    if not math.isfinite(value):
        raise ValueError("progress_0_1 must be finite")
    return max(0.0, min(1.0, value))
