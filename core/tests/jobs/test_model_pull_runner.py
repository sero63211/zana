"""Focused tests for the persistent bounded model pull runner."""

from __future__ import annotations

import threading
from types import SimpleNamespace

from zana_core.acquisition.admission import FilesystemAdmissionProvider
from zana_core.acquisition.models import (
    AcquisitionKind,
    AdmissionResult,
    NativeAcquisitionRequest,
)
from zana_core.acquisition.redact import sanitize_job_payload
from zana_core.acquisition.supervisor import CancelToken
from zana_core.db.models import Runtime
from zana_core.db.unit_of_work import UnitOfWork
from zana_core.domain.enums import (
    JobEventKind,
    JobKind,
    JobStatus,
    RuntimeKind,
    RuntimeSource,
    RuntimeStatus,
)
from zana_core.jobs.model_pull import (
    ModelPullRunner,
    ProgressPersistenceLimits,
    recover_interrupted_pull_jobs,
)
from zana_core.jobs.services import JobService
from zana_core.runtimes.discovery_service import runtime_identity


class RecordingTransport:
    def __init__(
        self,
        chunks: list[bytes] | None = None,
        *,
        open_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.chunks = list(chunks or [])
        self.open_error = open_error
        self.close_error = close_error
        self.calls: list[tuple[str, str, bytes | None]] = []
        self.closed = False

    def open_stream(
        self,
        method: str,
        url: str,
        *,
        headers=None,  # noqa: ANN001
        body: bytes | None = None,
        timeout: float,
    ) -> object:
        self.calls.append((method, url, body))
        if self.open_error is not None:
            raise self.open_error
        return iter(self.chunks)

    def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class GatedTransport(RecordingTransport):
    def __init__(self, chunks: list[bytes]) -> None:
        super().__init__(chunks)
        self.gate = threading.Event()
        self.first_yielded = threading.Event()

    def open_stream(
        self,
        method: str,
        url: str,
        *,
        headers=None,  # noqa: ANN001
        body: bytes | None = None,
        timeout: float,
    ) -> object:
        self.calls.append((method, url, body))
        return self._stream()

    def _stream(self) -> object:
        for chunk in self.chunks:
            yield chunk
            self.first_yielded.set()
            self.gate.wait(timeout=5)


class AllowedAdmission:
    def admit(self, request: NativeAcquisitionRequest) -> AdmissionResult:
        return AdmissionResult(allowed=True, reason="ok", conservative_reserve_bytes=0)


class DeniedAdmission:
    def admit(self, request: NativeAcquisitionRequest) -> AdmissionResult:  # noqa: ARG001
        return AdmissionResult(
            allowed=False,
            reason="DISK_INSUFFICIENT",
            conservative_reserve_bytes=1024,
        )


class FakeDiscovery:
    def __init__(self, model=None, descriptor=None) -> None:  # noqa: ANN001
        self.model = model
        self.descriptor = descriptor if descriptor is not None else object()
        self.calls: list[tuple[object, str]] = []

    def confirm_model(self, session_factory, snapshot, model_reference):  # noqa: ANN001, ARG001
        self.calls.append((snapshot, model_reference))
        return self.descriptor, self.model


def _discovered_model(model_id: str = "qwen2:1.5b", digest: str = "sha256:xyz"):
    return SimpleNamespace(model_id=model_id, digest=digest)


def _seed_pull(
    session_factory,
    *,
    endpoint: str = "http://127.0.0.1:11434",
    model_reference: str = "qwen2:1.5b",
    expected_size_bytes: int | None = 100,
    user_approved: bool = True,
    deadline_seconds: float = 60.0,
    source: RuntimeSource = RuntimeSource.AUTO,
    status: RuntimeStatus = RuntimeStatus.ONLINE,
    runtime_status_override: RuntimeStatus | None = None,
) -> tuple[int, int]:
    with UnitOfWork(session_factory) as uow:
        runtime = Runtime(
            kind=RuntimeKind.OLLAMA,
            endpoint=endpoint,
            source=source,
            status=runtime_status_override or status,
        )
        uow.runtimes.add(runtime)
        uow.session.flush()
        service = JobService(uow)
        job = service.create_job(
            JobKind.MODEL_PULL,
            phase="queued",
            message=model_reference,
        )
        job.error_json = sanitize_job_payload(
            runtime_id=runtime.id,
            model_reference=model_reference,
            expected_size_bytes=expected_size_bytes,
            user_approved=user_approved,
            deadline_seconds=deadline_seconds,
            runtime_kind=runtime.kind.value,
            runtime_source=runtime.source.value,
            runtime_status=runtime.status.value,
            runtime_identity=runtime_identity(
                runtime.kind,
                runtime.endpoint,
                runtime.source,
            ),
        )
    return job.id, runtime.id


def _fetch(session_factory, job_id: int):
    with UnitOfWork(session_factory) as uow:
        job = uow.jobs.get(job_id)
        events = JobService(uow).list_events(job_id)
        return job, events


def _runner(session_factory, *, discovery=None, progress_limits=None) -> ModelPullRunner:
    return ModelPullRunner(
        session_factory,
        discovery=discovery or FakeDiscovery(_discovered_model()),
        progress_limits=progress_limits,
        clock=lambda: 0.0,
    )


def test_queue_to_running_to_bounded_progress_to_discovery_success(
    session_factory,
) -> None:
    job_id, _ = _seed_pull(session_factory)
    transport = RecordingTransport(
        [
            b'{"status":"downloading","total":10,"completed":2}\n',
            b'{"status":"downloading","total":10,"completed":10}\n',
            b'{"status":"success"}\n',
        ]
    )
    discovery = FakeDiscovery(_discovered_model())
    _runner(session_factory, discovery=discovery).execute(
        job_id,
        transport=transport,
        admission=AllowedAdmission(),
        cancel=CancelToken(0),
    )

    job, events = _fetch(session_factory, job_id)
    assert job is not None
    assert job.status is JobStatus.SUCCEEDED
    assert job.progress_0_1 == 1.0
    assert job.error_json["code"] == "ACQUISITION_SUCCEEDED"
    assert job.error_json["model"]["digest"] == "sha256:xyz"
    assert transport.closed is True
    assert len(discovery.calls) == 1
    kinds = [event.kind for event in events]
    assert JobEventKind.STATUS_CHANGED in kinds
    assert JobEventKind.PROGRESS in kinds
    progress_values = [
        event.progress_0_1 for event in events if event.kind == JobEventKind.PROGRESS
    ]
    assert all(value < 1.0 for value in progress_values)
    assert job.progress_0_1 == 1.0


def test_transport_failure_is_honest_and_discovery_not_called(session_factory) -> None:
    job_id, _ = _seed_pull(session_factory)
    transport = RecordingTransport(open_error=RuntimeError("transport boom with secret"))
    discovery = FakeDiscovery(_discovered_model())
    _runner(session_factory, discovery=discovery).execute(
        job_id,
        transport=transport,
        admission=AllowedAdmission(),
        cancel=CancelToken(0),
    )
    job, _ = _fetch(session_factory, job_id)
    assert job is not None
    assert job.status is JobStatus.FAILED
    assert job.error_json["code"] == "TRANSPORT_FAILED"
    assert "secret" not in job.error_json["message"]
    assert discovery.calls == []
    assert transport.closed is True


def test_protocol_failure_is_terminal(session_factory) -> None:
    job_id, _ = _seed_pull(session_factory)
    transport = RecordingTransport([b"not json\n"])
    _runner(session_factory).execute(
        job_id,
        transport=transport,
        admission=AllowedAdmission(),
        cancel=CancelToken(0),
    )
    job, _ = _fetch(session_factory, job_id)
    assert job is not None
    assert job.status is JobStatus.FAILED
    assert job.error_json["code"] == "STREAM_MALFORMED"


def test_admission_denial_blocks_transport(session_factory) -> None:
    job_id, _ = _seed_pull(session_factory)
    transport = RecordingTransport([b'{"status":"success"}\n'])
    _runner(session_factory).execute(
        job_id,
        transport=transport,
        admission=DeniedAdmission(),
        cancel=CancelToken(0),
    )
    job, _ = _fetch(session_factory, job_id)
    assert job is not None
    assert job.status is JobStatus.FAILED
    assert job.error_json["code"] == "ADMISSION_DENIED"
    assert transport.calls == []


def test_cancel_before_start_never_opens_transport(session_factory) -> None:
    job_id, _ = _seed_pull(session_factory)
    transport = RecordingTransport([b'{"status":"success"}\n'])
    token = CancelToken(0)
    token.cancel()
    _runner(session_factory).execute(
        job_id,
        transport=transport,
        admission=AllowedAdmission(),
        cancel=token,
    )
    job, _ = _fetch(session_factory, job_id)
    assert job is not None
    assert job.status is JobStatus.CANCELLED
    assert job.error_json["code"] == "CANCELLED"
    assert transport.calls == []


def test_cancel_during_transport_is_cooperative(session_factory) -> None:
    job_id, _ = _seed_pull(session_factory)
    transport = GatedTransport(
        [
            b'{"status":"downloading","total":10,"completed":1}\n',
            b'{"status":"downloading","total":10,"completed":2}\n',
        ]
    )
    token = CancelToken(0)
    errors: list[Exception] = []

    def run() -> None:
        try:
            _runner(session_factory).execute(
                job_id,
                transport=transport,
                admission=AllowedAdmission(),
                cancel=token,
            )
        except Exception as error:  # noqa: BLE001
            errors.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    assert transport.first_yielded.wait(timeout=3)
    token.cancel()
    transport.gate.set()
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert errors == []
    job, _ = _fetch(session_factory, job_id)
    assert job is not None
    assert job.status is JobStatus.CANCELLED
    assert transport.closed is True


def test_runtime_deleted_after_queue_blocks_before_transport(session_factory) -> None:
    job_id, runtime_id = _seed_pull(session_factory)
    with UnitOfWork(session_factory) as uow:
        runtime = uow.runtimes.get(runtime_id)
        assert runtime is not None
        uow.runtimes.delete(runtime)
    transport = RecordingTransport([b'{"status":"success"}\n'])
    _runner(session_factory).execute(
        job_id,
        transport=transport,
        admission=AllowedAdmission(),
        cancel=CancelToken(0),
    )
    job, _ = _fetch(session_factory, job_id)
    assert job is not None
    assert job.status is JobStatus.FAILED
    assert job.error_json["code"] == "RUNTIME_CHANGED"
    assert transport.calls == []


def test_runtime_endpoint_change_blocks_before_transport(session_factory) -> None:
    job_id, runtime_id = _seed_pull(session_factory)
    with UnitOfWork(session_factory) as uow:
        runtime = uow.runtimes.get(runtime_id)
        assert runtime is not None
        runtime.endpoint = "http://127.0.0.1:9999"
    transport = RecordingTransport([b'{"status":"success"}\n'])
    _runner(session_factory).execute(
        job_id,
        transport=transport,
        admission=AllowedAdmission(),
        cancel=CancelToken(0),
    )
    job, _ = _fetch(session_factory, job_id)
    assert job is not None
    assert job.status is JobStatus.FAILED
    assert job.error_json["code"] == "RUNTIME_CHANGED"
    assert transport.calls == []


def test_runtime_kind_change_blocks_before_transport(session_factory) -> None:
    job_id, runtime_id = _seed_pull(session_factory)
    with UnitOfWork(session_factory) as uow:
        runtime = uow.runtimes.get(runtime_id)
        assert runtime is not None
        runtime.kind = RuntimeKind.OPENAI_COMPATIBLE
    transport = RecordingTransport([b'{"status":"success"}\n'])
    _runner(session_factory).execute(
        job_id,
        transport=transport,
        admission=AllowedAdmission(),
        cancel=CancelToken(0),
    )
    job, _ = _fetch(session_factory, job_id)
    assert job is not None
    assert job.status is JobStatus.FAILED
    assert job.error_json["code"] == "RUNTIME_CHANGED"
    assert transport.calls == []


def test_runtime_disabled_blocks_before_transport(session_factory) -> None:
    job_id, runtime_id = _seed_pull(session_factory)
    with UnitOfWork(session_factory) as uow:
        runtime = uow.runtimes.get(runtime_id)
        assert runtime is not None
        runtime.status = RuntimeStatus.OFFLINE
    transport = RecordingTransport([b'{"status":"success"}\n'])
    _runner(session_factory).execute(
        job_id,
        transport=transport,
        admission=AllowedAdmission(),
        cancel=CancelToken(0),
    )
    job, _ = _fetch(session_factory, job_id)
    assert job is not None
    assert job.status is JobStatus.FAILED
    assert job.error_json["code"] == "RUNTIME_NOT_ENABLED"
    assert transport.calls == []


def test_restart_recovery_marks_stale_pulls_interrupted(session_factory) -> None:
    pending_id, _ = _seed_pull(session_factory)
    running_id, _ = _seed_pull(session_factory)
    with UnitOfWork(session_factory) as uow:
        JobService(uow).transition_job(running_id, JobStatus.RUNNING)
    count = recover_interrupted_pull_jobs(session_factory)
    assert count == 2
    for job_id in (pending_id, running_id):
        job, _ = _fetch(session_factory, job_id)
        assert job is not None
        assert job.status is JobStatus.FAILED
        assert job.error_json["code"] == "INTERRUPTED_ON_RESTART"
        assert job.phase == "interrupted"


def test_progress_events_are_bounded(session_factory) -> None:
    job_id, _ = _seed_pull(session_factory)
    chunks = [
        (b'{"status":"downloading","total":100,"completed":%d}\n' % index) for index in range(20)
    ]
    chunks.append(b'{"status":"success"}\n')
    transport = RecordingTransport(chunks)
    discovery = FakeDiscovery(_discovered_model())
    _runner(
        session_factory,
        discovery=discovery,
        progress_limits=ProgressPersistenceLimits(
            max_events=3,
            min_interval_seconds=0.0,
            max_message_chars=5,
        ),
    ).execute(
        job_id,
        transport=transport,
        admission=AllowedAdmission(),
        cancel=CancelToken(0),
    )
    job, events = _fetch(session_factory, job_id)
    assert job is not None
    assert job.status is JobStatus.SUCCEEDED
    progress_events = [event for event in events if event.kind == JobEventKind.PROGRESS]
    assert len(progress_events) <= 3
    assert all(len(event.message) <= 5 for event in progress_events)
    values = [event.progress_0_1 for event in progress_events]
    assert values == sorted(values)


def test_filesystem_admission_unknown_insufficient_allowed(tmp_path) -> None:
    root = tmp_path / "data"
    root.mkdir()
    provider = FilesystemAdmissionProvider(root, reserve_bytes=100)

    def request(expected: int | None):
        return NativeAcquisitionRequest(
            kind=AcquisitionKind.OLLAMA_PULL,
            endpoint="http://127.0.0.1:11434",
            model_reference="m",
            expected_size_bytes=expected,
            user_approved=True,
        )

    assert provider.admit(request(None)).allowed is False
    assert provider.admit(request(0)).allowed is False
    assert provider.admit(request(100)).allowed is True
    huge = NativeAcquisitionRequest(
        kind=AcquisitionKind.OLLAMA_PULL,
        endpoint="http://127.0.0.1:11434",
        model_reference="m",
        expected_size_bytes=1 << 40,
        user_approved=True,
    )
    assert provider.admit(huge).allowed is False


def test_filesystem_admission_lease_conflict(tmp_path) -> None:
    root = tmp_path / "data"
    root.mkdir()
    provider = FilesystemAdmissionProvider(
        root,
        reserve_bytes=10,
        lease_conflict=lambda: True,
    )
    request = NativeAcquisitionRequest(
        kind=AcquisitionKind.OLLAMA_PULL,
        endpoint="http://127.0.0.1:11434",
        model_reference="m",
        expected_size_bytes=100,
        user_approved=True,
    )
    result = provider.admit(request)
    assert result.allowed is False
    assert result.reason == "LEASE_CONFLICT"
