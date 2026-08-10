"""Bounded persistent acquisition supervisor with a lazy worker thread."""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from zana_core.acquisition.protocols import (
    AdmissionProvider,
    NativeStreamTransport,
)
from zana_core.acquisition.redact import sanitize_terminal_error
from zana_core.db.unit_of_work import UnitOfWork
from zana_core.domain.enums import JobStatus
from zana_core.jobs.model_pull import (
    ModelPullRunner,
    ProgressPersistenceLimits,
)
from zana_core.jobs.services import JobNotFoundError, JobService
from zana_core.jobs.state_machine import (
    TERMINAL_JOB_STATES,
    InvalidJobTransitionError,
)


class QueueFullError(ValueError):
    """Raised when the bounded acquisition queue is full."""


class DispatchError(RuntimeError):
    """Raised when a queued job cannot be handed to the worker safely."""


class AcquisitionShutdownError(RuntimeError):
    """Raised when the supervisor or transport cannot clean up deterministically."""


class CancelToken:
    """Thread-safe cooperative cancellation flag for one acquisition."""

    def __init__(self, generation: int) -> None:
        self._generation = generation
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def generation(self) -> int:
        return self._generation


class AcquisitionSupervisor:
    """Owns one lazy worker and a bounded FIFO of persisted pull job ids.

    No worker thread exists until the first dispatch, and the worker blocks
    on a condition rather than polling. Shutdown is explicit and deterministic.
    """

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        transport: NativeStreamTransport,
        admission: AdmissionProvider,
        discovery: Any,
        runner: ModelPullRunner | None = None,
        progress_limits: ProgressPersistenceLimits | None = None,
        max_queue: int = 8,
        clock: Callable[[], float] | None = None,
        thread_factory: Callable[[Callable[[], None]], threading.Thread] | None = None,
    ) -> None:
        if type(max_queue) is not int or max_queue < 1 or max_queue > 64:
            raise ValueError("max_queue must be an int in [1, 64]")
        self._session_factory = session_factory
        self._transport = transport
        self._admission = admission
        self._discovery = discovery
        self._runner = runner or ModelPullRunner(
            session_factory,
            discovery=discovery,
            progress_limits=progress_limits,
            clock=clock,
        )
        self._max_queue = max_queue
        self._condition = threading.Condition()
        self._pending: deque[int] = deque()
        self._tokens: dict[int, CancelToken] = {}
        self._worker: threading.Thread | None = None
        self._stop = False
        self._generation = 0

        def default_factory(target: Callable[[], None]) -> threading.Thread:
            return threading.Thread(
                target=target,
                name="zana-acquisition-worker",
                daemon=True,
            )

        self._thread_factory = thread_factory or default_factory

    @property
    def worker_started(self) -> bool:
        return self._worker is not None

    @property
    def pending_count(self) -> int:
        with self._condition:
            return len(self._pending)

    def dispatch(self, job_id: int) -> None:
        if type(job_id) is not int or job_id <= 0:
            raise ValueError("job_id must be a positive int")
        token = CancelToken(self._generation)
        with self._condition:
            if self._stop:
                raise DispatchError("Acquisition supervisor is shutting down.")
            if job_id in self._tokens:
                raise DispatchError("Job is already queued or running.")
            if len(self._tokens) >= self._max_queue:
                raise QueueFullError("Acquisition queue is full.")
            self._pending.append(job_id)
            self._tokens[job_id] = token
            if self._worker is None or not self._worker.is_alive():
                self._worker = None
                try:
                    worker = self._thread_factory(self._run_forever)
                    worker.start()
                except Exception:  # noqa: BLE001 - dispatch failure is honest
                    self._worker = None
                    self._pending.remove(job_id)
                    self._tokens.pop(job_id, None)
                    raise DispatchError("Acquisition worker could not be started.") from None
                self._worker = worker
            self._condition.notify()

    def cancel(self, job_id: int) -> bool:
        with self._condition:
            token = self._tokens.get(job_id)
            if token is not None and token.generation != self._generation:
                token = None
        if token is not None:
            token.cancel()
            return True
        return False

    def shutdown(self, timeout: float = 5.0) -> None:
        worker = None
        with self._condition:
            self._generation += 1
            self._stop = True
            for token in self._tokens.values():
                token.cancel()
            self._condition.notify_all()
            worker = self._worker
        cleanup_failed = False
        try:
            self._transport.close()
        except Exception:  # noqa: BLE001 - cleanup is reported, never silent
            cleanup_failed = True
        alive = False
        if worker is not None:
            worker.join(timeout=timeout)
            alive = worker.is_alive()
            if not alive:
                with self._condition:
                    if self._worker is worker:
                        self._worker = None
        interruption_failed = False
        try:
            self._mark_pending_interrupted()
        except Exception:  # noqa: BLE001 - interruption persistence is sanitized
            interruption_failed = True
        if alive or cleanup_failed or interruption_failed:
            details = []
            if alive:
                details.append("worker did not stop cleanly")
            if cleanup_failed:
                details.append("transport cleanup failed")
            if interruption_failed:
                details.append("interrupted-job persistence failed")
            raise AcquisitionShutdownError(
                "Acquisition shutdown could not complete: " + "; ".join(details) + "."
            )

    def _run_forever(self) -> None:
        worker_generation = self._generation
        try:
            while True:
                with self._condition:
                    while not self._pending and not self._stop:
                        self._condition.wait()
                    if self._stop:
                        break
                    job_id = self._pending.popleft()
                    token = self._tokens.get(job_id)
                    if token is None:
                        token = CancelToken(worker_generation)
                    if token.generation != worker_generation:
                        continue
                try:
                    self._runner.execute(
                        job_id,
                        transport=self._transport,
                        admission=self._admission,
                        cancel=token,
                    )
                except Exception:  # noqa: BLE001 - never crash the worker
                    with suppress(Exception):
                        ModelPullRunner.mark_job_failed(
                            self._session_factory,
                            job_id,
                            "ACQUISITION_RUNNER_FAILED",
                            "Model acquisition could not be executed.",
                        )
                finally:
                    with self._condition:
                        self._tokens.pop(job_id, None)
        finally:
            with self._condition:
                self._worker = None
                self._condition.notify_all()

    def _mark_pending_interrupted(self) -> None:
        interrupted: list[int] = []
        with self._condition:
            pending = list(self._pending)
            interrupted = pending + [job_id for job_id in self._tokens if job_id not in pending]
            self._pending.clear()
            self._tokens.clear()
        if not interrupted:
            return
        with UnitOfWork(self._session_factory) as uow:
            service = JobService(uow)
            for job_id in interrupted:
                try:
                    job = service.get_job(job_id)
                except JobNotFoundError:
                    continue
                if job.status in TERMINAL_JOB_STATES:
                    continue
                try:
                    service.transition_job(
                        job_id,
                        JobStatus.FAILED,
                        phase="interrupted",
                        message="Model acquisition was interrupted by shutdown.",
                        error=sanitize_terminal_error(
                            code="INTERRUPTED_ON_SHUTDOWN",
                            message="Model acquisition was interrupted by shutdown.",
                        ),
                    )
                except InvalidJobTransitionError:
                    continue
