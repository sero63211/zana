"""Focused tests for lazy bounded acquisition supervisor lifecycle."""

from __future__ import annotations

import threading

import pytest

from zana_core.acquisition.supervisor import (
    AcquisitionShutdownError,
    AcquisitionSupervisor,
    DispatchError,
    QueueFullError,
)
from zana_core.db.unit_of_work import UnitOfWork
from zana_core.domain.enums import JobKind
from zana_core.jobs.services import JobService


class FakeTransport:
    def __init__(self, *, close_error: Exception | None = None) -> None:
        self.closed = False
        self.close_error = close_error

    def open_stream(self, method, url, *, headers=None, body=None, timeout):  # noqa: ANN001, ARG001
        raise AssertionError("transport must never be opened in supervisor tests")

    def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class EventTransport(FakeTransport):
    def __init__(self) -> None:
        super().__init__()
        self.closed_event = threading.Event()

    def close(self) -> None:
        self.closed = True
        self.closed_event.set()
        if self.close_error is not None:
            raise self.close_error


class FakeAdmission:
    def admit(self, request):  # noqa: ANN001
        raise AssertionError("admission must never run in supervisor tests")


class BlockingRunner:
    """Runner that blocks until released; no network or database work."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls: list[int] = []

    def execute(
        self,
        job_id: int,
        *,
        transport,
        admission,
        cancel,
    ) -> None:
        self.calls.append(job_id)
        self.started.set()
        self.release.wait(timeout=5)


class FlakyRunner:
    """Raises once, proving a dead worker pointer cannot strand dispatches."""

    def __init__(self) -> None:
        self.calls: list[int] = []
        self.first = True
        self.started = threading.Event()
        self.done = threading.Event()

    def execute(
        self,
        job_id: int,
        *,
        transport,
        admission,
        cancel,
    ) -> None:
        del transport, admission, cancel
        self.calls.append(job_id)
        self.started.set()
        if self.first:
            self.first = False
            raise RuntimeError("injected runner boom")
        self.done.set()


def _supervisor(
    session_factory,
    *,
    runner: BlockingRunner | None = None,
    transport: FakeTransport | None = None,
    max_queue: int = 1,
) -> AcquisitionSupervisor:
    return AcquisitionSupervisor(
        session_factory=session_factory,
        transport=transport or FakeTransport(),
        admission=FakeAdmission(),
        discovery=object(),
        runner=runner or BlockingRunner(),
        max_queue=max_queue,
    )


def _pending_job(session_factory) -> int:
    with UnitOfWork(session_factory) as uow:
        job = JobService(uow).create_job(JobKind.MODEL_PULL, phase="queued", message="m")
        return job.id


def test_no_worker_thread_while_idle(session_factory) -> None:
    supervisor = _supervisor(session_factory)
    assert supervisor.worker_started is False
    assert supervisor.pending_count == 0
    supervisor.shutdown()
    assert supervisor.worker_started is False


def test_dispatch_starts_one_lazy_worker(session_factory) -> None:
    runner = BlockingRunner()
    supervisor = _supervisor(session_factory, runner=runner)
    supervisor.dispatch(1)
    assert runner.started.wait(timeout=3)
    assert supervisor.worker_started is True
    runner.release.set()
    supervisor.shutdown()
    assert supervisor.worker_started is False


def test_queue_is_bounded_and_duplicate_dispatch_is_rejected(session_factory) -> None:
    runner = BlockingRunner()
    supervisor = _supervisor(session_factory, runner=runner)
    supervisor.dispatch(1)
    assert runner.started.wait(timeout=3)
    with pytest.raises(QueueFullError):
        supervisor.dispatch(2)
    with pytest.raises(DispatchError):
        supervisor.dispatch(1)
    runner.release.set()
    supervisor.shutdown()


def test_dispatch_failure_is_honest_and_leaves_no_thread(session_factory) -> None:
    def broken_thread_factory(target):  # noqa: ANN001
        del target
        raise RuntimeError("thread start boom")

    supervisor = AcquisitionSupervisor(
        session_factory=session_factory,
        transport=FakeTransport(),
        admission=FakeAdmission(),
        discovery=object(),
        runner=BlockingRunner(),
        thread_factory=broken_thread_factory,
    )
    with pytest.raises(DispatchError):
        supervisor.dispatch(7)
    assert supervisor.worker_started is False
    assert supervisor.pending_count == 0


def test_shutdown_marks_pending_jobs_interrupted(session_factory) -> None:
    first = _pending_job(session_factory)
    second = _pending_job(session_factory)
    runner = BlockingRunner()
    supervisor = _supervisor(session_factory, runner=runner, max_queue=2)
    supervisor.dispatch(first)
    assert runner.started.wait(timeout=3)
    supervisor.dispatch(second)

    shutdown_result: list[Exception | None] = []

    def shutdown() -> None:
        try:
            supervisor.shutdown()
            shutdown_result.append(None)
        except Exception as error:  # noqa: BLE001
            shutdown_result.append(error)

    thread = threading.Thread(target=shutdown)
    thread.start()
    thread.join(timeout=0.2)
    runner.release.set()
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert shutdown_result == [None]
    with UnitOfWork(session_factory) as uow:
        second_job = uow.jobs.get(second)
        assert second_job is not None
        assert second_job.status.value == "FAILED"
        assert second_job.error_json["code"] == "INTERRUPTED_ON_SHUTDOWN"
        first_job = uow.jobs.get(first)
        assert first_job is not None


def test_shutdown_reports_transport_cleanup_failure(session_factory) -> None:
    transport = FakeTransport(close_error=RuntimeError("close boom with secret"))
    supervisor = _supervisor(session_factory, transport=transport)
    with pytest.raises(AcquisitionShutdownError) as raised:
        supervisor.shutdown()
    assert "close boom" not in str(raised.value)
    assert "secret" not in str(raised.value)


def test_shutdown_closes_transport_before_joining_worker(session_factory) -> None:
    runner = BlockingRunner()
    transport = EventTransport()
    supervisor = _supervisor(session_factory, runner=runner, transport=transport)
    supervisor.dispatch(1)
    assert runner.started.wait(timeout=3)

    thread = threading.Thread(target=supervisor.shutdown)
    thread.start()
    assert transport.closed_event.wait(timeout=3)
    assert not runner.release.is_set()
    runner.release.set()
    thread.join(timeout=3)
    assert not thread.is_alive()


def test_queue_capacity_counts_active_and_pending_total(session_factory) -> None:
    runner = BlockingRunner()
    supervisor = _supervisor(session_factory, runner=runner, max_queue=2)
    supervisor.dispatch(1)
    assert runner.started.wait(timeout=3)
    supervisor.dispatch(2)
    with pytest.raises(QueueFullError):
        supervisor.dispatch(3)
    runner.release.set()
    supervisor.shutdown()


def test_runner_exception_cannot_strand_future_dispatch(session_factory) -> None:
    runner = FlakyRunner()
    supervisor = AcquisitionSupervisor(
        session_factory=session_factory,
        transport=FakeTransport(),
        admission=FakeAdmission(),
        discovery=object(),
        runner=runner,  # type: ignore[arg-type]
        max_queue=2,
    )
    supervisor.dispatch(1)
    assert runner.started.wait(timeout=3)
    supervisor.dispatch(2)
    assert runner.done.wait(timeout=3)
    supervisor.shutdown()


def test_shutdown_aggregates_interruption_persistence_failure(session_factory) -> None:
    runner = BlockingRunner()
    transport = FakeTransport()
    supervisor = _supervisor(session_factory, runner=runner, transport=transport)
    supervisor.dispatch(1)
    assert runner.started.wait(timeout=3)

    original = supervisor._mark_pending_interrupted

    def failing_mark() -> None:
        raise RuntimeError("interruption boom with secret")

    supervisor._mark_pending_interrupted = failing_mark  # type: ignore[method-assign]
    with pytest.raises(AcquisitionShutdownError) as raised:
        supervisor.shutdown()
    runner.release.set()
    assert "interrupted-job persistence failed" in str(raised.value)
    assert "boom" not in str(raised.value)
    assert "secret" not in str(raised.value)
    original()
