"""Focused API tests for model pull dispatch, cancellation, and redaction."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from zana_core.acquisition.models import (
    AdmissionResult,
    NativeAcquisitionRequest,
)
from zana_core.acquisition.supervisor import (
    AcquisitionShutdownError,
    AcquisitionSupervisor,
    DispatchError,
    QueueFullError,
)
from zana_core.db.unit_of_work import UnitOfWork
from zana_core.domain.enums import JobKind, RuntimeStatus
from zana_core.jobs.services import JobService
from zana_core.main import create_app


class NoopSupervisor:
    def __init__(self) -> None:
        self.dispatched: list[int] = []

    def dispatch(self, job_id: int) -> None:
        self.dispatched.append(job_id)

    def cancel(self, job_id: int) -> bool:  # noqa: ARG001
        return False

    def shutdown(self, timeout: float = 5.0) -> None:  # noqa: ARG001
        return None


class RaisingSupervisor:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.dispatched: list[int] = []

    def dispatch(self, job_id: int) -> None:
        self.dispatched.append(job_id)
        raise self.error

    def cancel(self, job_id: int) -> bool:  # noqa: ARG001
        return False

    def shutdown(self, timeout: float = 5.0) -> None:  # noqa: ARG001
        return None


class FixedAdmission:
    def __init__(self, result: AdmissionResult) -> None:
        self.result = result

    def admit(self, request: NativeAcquisitionRequest) -> AdmissionResult:  # noqa: ARG001
        return self.result


def _client(
    database,
    *,
    supervisor=None,  # noqa: ANN001
    admission=None,  # noqa: ANN001
) -> TestClient:
    app = create_app(
        token="test-token-abc123",
        database_path=database.path,
        acquisition_supervisor=supervisor if supervisor is not None else NoopSupervisor(),
        acquisition_admission=admission,
    )
    return TestClient(app)


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token-abc123"}


def _seed_ollama(client: TestClient, database) -> int:
    response = client.post(
        "/api/v1/runtimes/manual",
        json={"kind": "ollama", "endpoint": "http://127.0.0.1:11434"},
        headers=_headers(),
    )
    assert response.status_code == 201
    runtime_id = response.json()["id"]
    with UnitOfWork(database.session_factory) as uow:
        runtime = uow.runtimes.get(runtime_id)
        assert runtime is not None
        runtime.status = RuntimeStatus.ONLINE
    return runtime_id


def _pull_payload(runtime_id: int, **overrides) -> dict[str, object]:
    payload: dict[str, object] = {
        "runtime_id": runtime_id,
        "model_reference": "qwen2:1.5b",
        "user_approved": True,
        "expected_size_bytes": 1_000_000,
    }
    payload.update(overrides)
    return payload


class NoopRunner:
    def execute(
        self,
        job_id: int,
        *,
        transport,
        admission,
        cancel,
    ) -> None:
        del job_id, transport, admission, cancel


class FakeTransport:
    def __init__(self, *, close_error: Exception | None = None) -> None:
        self.closed = False
        self.close_error = close_error

    def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


def test_pull_dispatches_and_persists_only_sanitized_state(database) -> None:
    supervisor = NoopSupervisor()
    client = _client(database, supervisor=supervisor)
    runtime_id = _seed_ollama(client, database)
    response = client.post(
        "/api/v1/models/pull",
        json=_pull_payload(runtime_id),
        headers=_headers(),
    )
    assert response.status_code == 201
    job = response.json()
    assert supervisor.dispatched == [job["id"]]
    raw = json.dumps(job)
    assert "http://127.0.0.1:11434" not in raw
    assert "request" not in job["error_json"]
    assert "plan" not in job["error_json"]
    assert "runtime_endpoint" not in job["error_json"]
    assert job["error_json"]["model_reference"] == "qwen2:1.5b"
    assert job["error_json"]["runtime_id"] == runtime_id


def test_dispatch_failure_persists_failed_job_not_fake_queued(database) -> None:
    supervisor = RaisingSupervisor(DispatchError("worker start boom"))
    client = _client(database, supervisor=supervisor)
    runtime_id = _seed_ollama(client, database)
    response = client.post(
        "/api/v1/models/pull",
        json=_pull_payload(runtime_id),
        headers=_headers(),
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ACQUISITION_DISPATCH_FAILED"
    job_id = response.json()["error"]["details"]["job_id"]
    fetched = client.get(f"/api/v1/jobs/{job_id}", headers=_headers())
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "FAILED"
    assert fetched.json()["error_json"]["code"] == "ACQUISITION_DISPATCH_FAILED"


def test_queue_full_persists_failed_job(database) -> None:
    supervisor = RaisingSupervisor(QueueFullError("queue full"))
    client = _client(database, supervisor=supervisor)
    runtime_id = _seed_ollama(client, database)
    response = client.post(
        "/api/v1/models/pull",
        json=_pull_payload(runtime_id),
        headers=_headers(),
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ACQUISITION_QUEUE_FULL"
    job_id = response.json()["error"]["details"]["job_id"]
    fetched = client.get(f"/api/v1/jobs/{job_id}", headers=_headers())
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "FAILED"
    assert fetched.json()["error_json"]["code"] == "ACQUISITION_QUEUE_FULL"


def test_unknown_disk_requirement_blocks_before_dispatch(database) -> None:
    supervisor = NoopSupervisor()
    client = _client(database, supervisor=supervisor)
    runtime_id = _seed_ollama(client, database)
    response = client.post(
        "/api/v1/models/pull",
        json=_pull_payload(runtime_id, expected_size_bytes=None),
        headers=_headers(),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "DISK_REQUIREMENT_UNKNOWN"
    assert supervisor.dispatched == []


def test_insufficient_disk_blocks_before_dispatch(database) -> None:
    supervisor = NoopSupervisor()
    client = _client(
        database,
        supervisor=supervisor,
        admission=FixedAdmission(
            AdmissionResult(
                allowed=False,
                reason="DISK_INSUFFICIENT",
                conservative_reserve_bytes=1024,
            )
        ),
    )
    runtime_id = _seed_ollama(client, database)
    response = client.post(
        "/api/v1/models/pull",
        json=_pull_payload(runtime_id),
        headers=_headers(),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "DISK_INSUFFICIENT"
    assert supervisor.dispatched == []


def test_cancel_is_idempotent_and_kind_safe(database) -> None:
    client = _client(database)
    runtime_id = _seed_ollama(client, database)
    pull = client.post(
        "/api/v1/models/pull",
        json=_pull_payload(runtime_id),
        headers=_headers(),
    )
    job_id = pull.json()["id"]
    cancelled = client.post(f"/api/v1/jobs/{job_id}/cancel", headers=_headers())
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"
    again = client.post(f"/api/v1/jobs/{job_id}/cancel", headers=_headers())
    assert again.status_code == 200
    assert again.json()["status"] == "CANCELLED"

    with UnitOfWork(database.session_factory) as uow:
        other = JobService(uow).create_job(JobKind.BUILD_ANALYSIS, phase="draft")
        other_id = other.id
    blocked = client.post(f"/api/v1/jobs/{other_id}/cancel", headers=_headers())
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "JOB_NOT_CANCELLABLE"


def test_pull_rejects_disabled_runtime(database) -> None:
    client = _client(database)
    response = client.post(
        "/api/v1/runtimes/manual",
        json={"kind": "ollama", "endpoint": "http://127.0.0.1:11434"},
        headers=_headers(),
    )
    runtime_id = response.json()["id"]
    with UnitOfWork(database.session_factory) as uow:
        runtime = uow.runtimes.get(runtime_id)
        assert runtime is not None
        runtime.status = RuntimeStatus.OFFLINE
    pull = client.post(
        "/api/v1/models/pull",
        json=_pull_payload(runtime_id),
        headers=_headers(),
    )
    assert pull.status_code == 409
    assert pull.json()["error"]["code"] == "RUNTIME_NOT_ENABLED"


def test_pull_rejects_unknown_runtime_without_dispatch(database) -> None:
    supervisor = NoopSupervisor()
    client = _client(database, supervisor=supervisor)
    created = client.post(
        "/api/v1/runtimes/manual",
        json={"kind": "ollama", "endpoint": "http://127.0.0.1:11434"},
        headers=_headers(),
    )
    assert created.status_code == 201
    runtime_id = created.json()["id"]
    pull = client.post(
        "/api/v1/models/pull",
        json=_pull_payload(runtime_id),
        headers=_headers(),
    )
    assert pull.status_code == 409
    assert pull.json()["error"]["code"] == "RUNTIME_NOT_ENABLED"
    assert supervisor.dispatched == []
    with UnitOfWork(database.session_factory) as uow:
        assert uow.jobs.list() == []


def test_rejected_secret_reference_never_persisted_or_exposed(database) -> None:
    supervisor = NoopSupervisor()
    client = _client(database, supervisor=supervisor)
    runtime_id = _seed_ollama(client, database)
    secret = "https://user:topsecret@example.com/pull?token=abc"
    response = client.post(
        "/api/v1/models/pull",
        json=_pull_payload(runtime_id, model_reference=secret),
        headers=_headers(),
    )
    assert response.status_code == 422
    assert "topsecret" not in response.text
    assert supervisor.dispatched == []
    with UnitOfWork(database.session_factory) as uow:
        assert uow.jobs.list() == []


@pytest.mark.parametrize(
    "reference",
    [" llama3.2:1b", "llama3.2:1b ", "\tllama3.2:1b", "llama3.2:1b\n", "\rllama3.2:1b"],
)
def test_whitespace_reference_rejected_without_job_or_detail(
    database,
    reference: str,
) -> None:
    supervisor = NoopSupervisor()
    client = _client(database, supervisor=supervisor)
    runtime_id = _seed_ollama(client, database)
    response = client.post(
        "/api/v1/models/pull",
        json=_pull_payload(runtime_id, model_reference=reference),
        headers=_headers(),
    )
    assert response.status_code == 422
    assert "llama3.2:1b" not in response.text
    assert "\t" not in response.text
    assert "\n" not in response.text
    assert "\r" not in response.text
    assert supervisor.dispatched == []
    with UnitOfWork(database.session_factory) as uow:
        assert uow.jobs.list() == []
    events = client.get("/api/v1/jobs/1/events", headers=_headers())
    assert events.status_code == 404


def test_queued_job_events_never_expose_endpoint_or_secret(database) -> None:
    client = _client(database)
    runtime_id = _seed_ollama(client, database)
    pull = client.post(
        "/api/v1/models/pull",
        json=_pull_payload(runtime_id),
        headers=_headers(),
    )
    assert pull.status_code == 201
    job_id = pull.json()["id"]
    events = client.get(f"/api/v1/jobs/{job_id}/events", headers=_headers())
    assert events.status_code == 200
    assert "http://127.0.0.1:11434" not in events.text
    assert "topsecret" not in events.text


def test_app_shutdown_closes_transport(database) -> None:
    transport = FakeTransport()
    supervisor = AcquisitionSupervisor(
        session_factory=database.session_factory,
        transport=transport,
        admission=FixedAdmission(
            AdmissionResult(allowed=True, reason="ok", conservative_reserve_bytes=0)
        ),
        discovery=object(),
        runner=NoopRunner(),  # type: ignore[arg-type]
    )
    app = create_app(
        token="test-token-abc123",
        database_path=database.path,
        acquisition_supervisor=supervisor,
    )
    with TestClient(app) as client:
        assert client.get("/api/v1/health").status_code == 401
        assert supervisor.worker_started is False
    assert transport.closed is True


def test_app_shutdown_reports_transport_cleanup_failure(database) -> None:
    transport = FakeTransport(close_error=RuntimeError("close boom with secret"))
    supervisor = AcquisitionSupervisor(
        session_factory=database.session_factory,
        transport=transport,
        admission=FixedAdmission(
            AdmissionResult(allowed=True, reason="ok", conservative_reserve_bytes=0)
        ),
        discovery=object(),
        runner=NoopRunner(),  # type: ignore[arg-type]
    )
    app = create_app(
        token="test-token-abc123",
        database_path=database.path,
        acquisition_supervisor=supervisor,
    )
    with pytest.raises(AcquisitionShutdownError), TestClient(app) as client:
        assert client.get("/api/v1/health").status_code == 401


def test_app_shutdown_closes_database_after_supervisor_error(database) -> None:
    transport = FakeTransport(close_error=RuntimeError("close boom"))
    supervisor = AcquisitionSupervisor(
        session_factory=database.session_factory,
        transport=transport,
        admission=FixedAdmission(
            AdmissionResult(allowed=True, reason="ok", conservative_reserve_bytes=0)
        ),
        discovery=object(),
        runner=NoopRunner(),  # type: ignore[arg-type]
    )
    closed: list[int] = []
    app = create_app(
        token="test-token-abc123",
        database_path=database.path,
        acquisition_supervisor=supervisor,
    )
    app_database = app.state.database
    original_close = app_database.close

    def spy_close() -> None:
        closed.append(1)
        original_close()

    app_database.close = spy_close  # type: ignore[method-assign]
    with pytest.raises(AcquisitionShutdownError), TestClient(app) as client:
        assert client.get("/api/v1/health").status_code == 401
    assert closed == [1]
