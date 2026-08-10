"""Persistent bounded execution for queued native model acquisition jobs."""

from __future__ import annotations

from collections.abc import Callable
from time import monotonic
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from zana_core.acquisition.admission import AdmissionDeniedError
from zana_core.acquisition.models import (
    AcquisitionKind,
    AcquisitionPolicy,
    AcquisitionState,
    NativeAcquisitionProgress,
    NativeAcquisitionRequest,
    NativeAcquisitionResult,
)
from zana_core.acquisition.protocols import (
    AdmissionProvider,
    CancellationToken,
    NativeStreamTransport,
)
from zana_core.acquisition.redact import (
    bounded_text,
    sanitize_model_reference,
    sanitize_terminal_error,
)
from zana_core.acquisition.service import AcquisitionService
from zana_core.db.models import Job
from zana_core.db.unit_of_work import UnitOfWork
from zana_core.domain.enums import (
    JobKind,
    JobStatus,
    RuntimeKind,
    RuntimeStatus,
)
from zana_core.jobs.services import JobNotFoundError, JobService
from zana_core.jobs.state_machine import (
    TERMINAL_JOB_STATES,
    InvalidJobTransitionError,
)
from zana_core.runtimes.discovery_service import RuntimeSnapshot


class ProgressPersistenceLimits(BaseModel):
    """Hard bounds for real-time progress persistence without unbounded events."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_events: int = Field(default=200, ge=1, le=2000)
    min_interval_seconds: float = Field(default=0.25, ge=0.0, le=60.0)
    max_message_chars: int = Field(default=256, ge=1, le=512)


class ModelPullRunner:
    """Executes one persisted model-pull job through injected boundaries."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        discovery: Any,
        progress_limits: ProgressPersistenceLimits | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._discovery = discovery
        self._progress_limits = progress_limits or ProgressPersistenceLimits()
        self._clock = clock or monotonic

    def execute(
        self,
        job_id: int,
        *,
        transport: NativeStreamTransport,
        admission: AdmissionProvider,
        cancel: CancellationToken,
    ) -> None:
        if cancel.is_cancelled():
            self._cancel_before_start(job_id)
            return
        snapshot: RuntimeSnapshot | None
        request: NativeAcquisitionRequest | None
        with UnitOfWork(self._session_factory) as uow:
            service = JobService(uow)
            job = self._load_job(service, job_id)
            if job is None:
                return
            snapshot, request = self._prepare(uow, service, job)
            if snapshot is None or request is None:
                return
            service.transition_job(
                job_id,
                JobStatus.RUNNING,
                phase="downloading",
                progress_0_1=0.0,
                message="Model acquisition started.",
            )
        if cancel.is_cancelled():
            self._cancel_before_start(job_id)
            return
        try:
            result = AcquisitionService(clock=self._clock).acquire(
                request,
                transport=transport,
                admission=admission,
                cancel=cancel,
                on_progress=self._progress_writer(job_id),
            )
        except AdmissionDeniedError:
            self._fail(
                job_id,
                "ADMISSION_DENIED",
                "Model acquisition was blocked by resource policy.",
            )
            return
        except Exception:  # noqa: BLE001 - failures are canonical below
            self._fail(
                job_id,
                "ACQUISITION_RUNNER_FAILED",
                "Model acquisition could not be executed.",
            )
            return
        if type(result) is not NativeAcquisitionResult:
            self._fail(
                job_id,
                "UNSUPPORTED_RUNTIME",
                "The selected runtime cannot perform a native model pull.",
            )
            return
        self._finalize(job_id, snapshot, request, result, cancel)

    @staticmethod
    def mark_job_failed(
        session_factory: sessionmaker[Session],
        job_id: int,
        code: str,
        message: str,
    ) -> None:
        with UnitOfWork(session_factory) as uow:
            service = JobService(uow)
            job = _get_optional(service, job_id)
            if job is None or job.kind != JobKind.MODEL_PULL:
                return
            if job.status in TERMINAL_JOB_STATES:
                return
            service.transition_job(
                job_id,
                JobStatus.FAILED,
                phase="failed",
                message=bounded_text(message),
                error=sanitize_terminal_error(code=code, message=message),
            )

    def _prepare(
        self,
        uow: UnitOfWork,
        service: JobService,
        job: Job,
    ) -> tuple[RuntimeSnapshot | None, NativeAcquisitionRequest | None]:
        raw = job.error_json
        if type(raw) is not dict:
            self._fail_with_service(
                service,
                job.id,
                "INVALID_PERSISTED_REQUEST",
                "The persisted request is invalid.",
            )
            return None, None
        runtime_id = raw.get("runtime_id")
        model_reference = raw.get("model_reference")
        expected_size_bytes = raw.get("expected_size_bytes")
        user_approved = raw.get("user_approved")
        deadline_seconds = raw.get("deadline_seconds")
        stored_identity = raw.get("runtime_identity")
        if (
            type(runtime_id) is not int
            or type(model_reference) is not str
            or type(stored_identity) is not str
            or type(user_approved) is not bool
            or type(deadline_seconds) not in (int, float)
        ):
            self._fail_with_service(
                service,
                job.id,
                "INVALID_PERSISTED_REQUEST",
                "The persisted request is invalid.",
            )
            return None, None
        if expected_size_bytes is not None and type(expected_size_bytes) is not int:
            self._fail_with_service(
                service,
                job.id,
                "INVALID_PERSISTED_REQUEST",
                "The persisted request is invalid.",
            )
            return None, None
        runtime = uow.runtimes.get(runtime_id)
        if runtime is None:
            self._fail_with_service(
                service,
                job.id,
                "RUNTIME_CHANGED",
                "The runtime was deleted after the pull was queued.",
            )
            return None, None
        snapshot = RuntimeSnapshot(
            id=runtime.id,
            kind=runtime.kind,
            endpoint=runtime.endpoint,
            source=runtime.source,
            status=runtime.status,
        )
        if snapshot.identity != stored_identity:
            self._fail_with_service(
                service,
                job.id,
                "RUNTIME_CHANGED",
                "The runtime identity changed after the pull was queued.",
            )
            return None, None
        if runtime.kind != RuntimeKind.OLLAMA:
            self._fail_with_service(
                service,
                job.id,
                "RUNTIME_CHANGED",
                "The runtime kind changed after the pull was queued.",
            )
            return None, None
        if runtime.status is not RuntimeStatus.ONLINE:
            self._fail_with_service(
                service,
                job.id,
                "RUNTIME_NOT_ENABLED",
                "The runtime is not enabled for a native pull.",
            )
            return None, None
        if type(deadline_seconds) is int:
            deadline = float(deadline_seconds)
        elif type(deadline_seconds) is float:
            deadline = deadline_seconds
        else:
            self._fail_with_service(
                service,
                job.id,
                "INVALID_PERSISTED_REQUEST",
                "The persisted request is invalid.",
            )
            return None, None
        try:
            reference = sanitize_model_reference(model_reference)
            request = NativeAcquisitionRequest(
                kind=AcquisitionKind.OLLAMA_PULL,
                endpoint=runtime.endpoint,
                model_reference=reference,
                policy=AcquisitionPolicy.LOCAL_ONLY,
                expected_size_bytes=expected_size_bytes,
                user_approved=user_approved,
                deadline_seconds=deadline,
            )
        except (ValidationError, ValueError):
            self._fail_with_service(
                service,
                job.id,
                "INVALID_PERSISTED_REQUEST",
                "The persisted request is invalid.",
            )
            return None, None
        if not request.user_approved:
            self._fail_with_service(
                service,
                job.id,
                "USER_APPROVAL_REQUIRED",
                "The persisted pull is not explicitly approved.",
            )
            return None, None
        return snapshot, request

    def _finalize(
        self,
        job_id: int,
        snapshot: RuntimeSnapshot,
        request: NativeAcquisitionRequest,
        result: NativeAcquisitionResult,
        cancel: CancellationToken,
    ) -> None:
        if cancel.is_cancelled():
            self._cancel_before_start(job_id)
            return
        if result.state is AcquisitionState.SUCCEEDED:
            if not self._job_is_active(job_id):
                return
            descriptor, model = self._discovery.confirm_model(
                self._session_factory,
                snapshot,
                request.model_reference,
            )
            with UnitOfWork(self._session_factory) as uow:
                service = JobService(uow)
                if self._load_job(service, job_id) is None:
                    return
                if cancel.is_cancelled():
                    self._cancel_with_service(service, job_id)
                    return
                if model is None or descriptor is None:
                    self._fail_with_service(
                        service,
                        job_id,
                        "DISCOVERY_CONFIRMATION_FAILED",
                        "The acquired model could not be confirmed by runtime discovery.",
                    )
                    return
                service.transition_job(
                    job_id,
                    JobStatus.SUCCEEDED,
                    phase="complete",
                    progress_0_1=1.0,
                    message="Model acquired and discovery confirmed.",
                    error={
                        "code": "ACQUISITION_SUCCEEDED",
                        "message": "Model acquired and discovery confirmed.",
                        "model": {
                            "model_id": bounded_text(model.model_id, max_chars=200),
                            "digest": bounded_text(model.digest or "", max_chars=128),
                        },
                    },
                )
            return
        if result.state is AcquisitionState.CANCELLED:
            self._cancel_before_start(job_id)
            return
        code = result.error_code or "ACQUISITION_FAILED"
        message = result.error_message or "Model acquisition failed."
        self._fail(job_id, code, message)

    @staticmethod
    def _load_job(service: JobService, job_id: int) -> Job | None:
        job = _get_optional(service, job_id)
        if job is None or job.kind != JobKind.MODEL_PULL:
            return None
        if job.status in TERMINAL_JOB_STATES:
            return None
        return job

    def _job_is_active(self, job_id: int) -> bool:
        with UnitOfWork(self._session_factory) as uow:
            return self._load_job(JobService(uow), job_id) is not None

    def _cancel_before_start(self, job_id: int) -> None:
        with UnitOfWork(self._session_factory) as uow:
            service = JobService(uow)
            job = self._load_job(service, job_id)
            if job is None:
                return
            self._cancel_with_service(service, job_id)

    @staticmethod
    def _cancel_with_service(service: JobService, job_id: int) -> None:
        try:
            service.transition_job(
                job_id,
                JobStatus.CANCELLED,
                phase="cancelled",
                message="Model acquisition cancelled.",
                error=sanitize_terminal_error(
                    code="CANCELLED",
                    message="Model acquisition cancelled.",
                ),
            )
        except InvalidJobTransitionError:
            return

    @staticmethod
    def _fail_with_service(
        service: JobService,
        job_id: int,
        code: str,
        message: str,
    ) -> None:
        try:
            service.transition_job(
                job_id,
                JobStatus.FAILED,
                phase="failed",
                message=bounded_text(message),
                error=sanitize_terminal_error(code=code, message=message),
            )
        except InvalidJobTransitionError:
            return

    def _fail(self, job_id: int, code: str, message: str) -> None:
        with UnitOfWork(self._session_factory) as uow:
            service = JobService(uow)
            if self._load_job(service, job_id) is None:
                return
            self._fail_with_service(service, job_id, code, message)

    def _progress_writer(
        self,
        job_id: int,
    ) -> Callable[[NativeAcquisitionProgress, int], None]:
        limits = self._progress_limits
        last_persisted_at = [0.0]
        persisted_events = [0]
        last_value = [0.0]

        def write(progress: NativeAcquisitionProgress, count: int) -> None:
            del count
            if persisted_events[0] >= limits.max_events:
                return
            now = self._clock()
            if (
                last_persisted_at[0] > 0.0
                and now - last_persisted_at[0] < limits.min_interval_seconds
            ):
                return
            value = progress.progress_0_1
            if value is not None and value >= 1.0:
                value = 0.99
            else:
                value = value if value is not None else 0.0
            if value < last_value[0]:
                return
            last_value[0] = value
            message = bounded_text(progress.status, max_chars=limits.max_message_chars)
            with UnitOfWork(self._session_factory) as uow:
                try:
                    JobService(uow).record_progress(
                        job_id,
                        value,
                        phase="downloading",
                        message=message,
                    )
                except InvalidJobTransitionError:
                    return
            persisted_events[0] += 1
            last_persisted_at[0] = now

        return write


def _get_optional(service: JobService, job_id: int) -> Job | None:
    try:
        return service.get_job(job_id)
    except JobNotFoundError:
        return None


def recover_interrupted_pull_jobs(session_factory: sessionmaker[Session]) -> int:
    """Mark stale PENDING/RUNNING model pulls interrupted; never auto-resume."""
    count = 0
    with UnitOfWork(session_factory) as uow:
        service = JobService(uow)
        for job in uow.jobs.list_active():
            if job.kind != JobKind.MODEL_PULL:
                continue
            try:
                service.transition_job(
                    job.id,
                    JobStatus.FAILED,
                    phase="interrupted",
                    message="Model acquisition was interrupted by a restart.",
                    error=sanitize_terminal_error(
                        code="INTERRUPTED_ON_RESTART",
                        message="Model acquisition was interrupted by a restart.",
                    ),
                )
            except InvalidJobTransitionError:
                continue
            count += 1
    return count
