"""Focused tests for system profile/doctor, runtime refresh, and model pull."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from zana_core.db.unit_of_work import UnitOfWork
from zana_core.domain.enums import (
    ModelIdentityStrength,
    RuntimeKind,
    RuntimeSource,
    RuntimeStatus,
)
from zana_core.main import create_app
from zana_core.runtimes.base import ModelDescriptor, RuntimeDescriptor, RuntimeProbeError
from zana_core.runtimes.registry import RuntimeProbeRegistry


def _model(
    model_id: str,
    *,
    runtime_id: str = "ollama-local",
    digest: str | None = "sha256:abc",
) -> ModelDescriptor:
    return ModelDescriptor(
        runtime_id=runtime_id,
        model_id=model_id,
        display_name=model_id,
        digest=digest,
        family="qwen",
        parameter_count=1_500_000_000,
        parameter_label="1.5B",
        format="gguf",
        quantization="Q4_K_M",
        size_bytes=1_000_000_000,
        context_length=32768,
        capabilities=["completion"],
        trainability="unknown",
        metadata_source="runtime",
        last_seen_at=datetime.now(UTC),
        identity_strength=ModelIdentityStrength.EXACT_DIGEST,
    )


def _descriptor(
    *,
    kind: RuntimeKind = RuntimeKind.OLLAMA,
    runtime_id: str = "ollama-local",
    endpoint: str = "http://127.0.0.1:11434",
    source: RuntimeSource = RuntimeSource.AUTO,
    status: RuntimeStatus = RuntimeStatus.ONLINE,
    registered: bool = True,
    models: list[ModelDescriptor] | None = None,
) -> RuntimeDescriptor:
    return RuntimeDescriptor(
        runtime_id=runtime_id,
        kind=kind,
        endpoint=endpoint,
        source=source,
        status=status,
        registered=registered,
        server_running=status == RuntimeStatus.ONLINE,
        installed=True,
        installed_not_running=status != RuntimeStatus.ONLINE,
        identified_vendor=None,
        evidence=["/api/tags 200"] if status == RuntimeStatus.ONLINE else [],
        warnings=[],
        error=None,
        models=models if models is not None else [_model(f"{runtime_id}:default")],
        last_seen_at=datetime.now(UTC),
    )


class FakeRegistry(RuntimeProbeRegistry):
    """Registry substitute returning protocol descriptors without network I/O."""

    def __init__(self, descriptors: list[RuntimeDescriptor] | None = None) -> None:
        super().__init__()
        self.descriptors = descriptors if descriptors is not None else [_descriptor()]

    def probe(self, targets: Any) -> list[RuntimeDescriptor]:  # noqa: ANN401
        return list(self.descriptors)


class RaisingRegistry(RuntimeProbeRegistry):
    """Registry substitute that fails discovery before any persistence."""

    def probe(self, targets: Any) -> list[RuntimeDescriptor]:  # noqa: ANN401
        raise RuntimeProbeError("injected discovery failure")


class NoopSupervisor:
    """Injected supervisor that records jobs without starting transport work."""

    def __init__(self) -> None:
        self.dispatched: list[int] = []

    def dispatch(self, job_id: int) -> None:
        self.dispatched.append(job_id)

    def cancel(self, job_id: int) -> bool:  # noqa: ARG001
        return False

    def shutdown(self, timeout: float = 5.0) -> None:  # noqa: ARG001
        return None


def _client(
    database,
    registry: RuntimeProbeRegistry | None = None,
    supervisor=None,  # noqa: ANN001
) -> TestClient:
    app = create_app(
        token="test-token-abc123",
        database_path=database.path,
        runtime_registry=registry if registry is not None else FakeRegistry(),
        acquisition_supervisor=supervisor if supervisor is not None else NoopSupervisor(),
    )
    return TestClient(app)


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token-abc123"}


def test_system_profile_requires_auth(database) -> None:
    client = _client(database)
    response = client.get("/api/v1/system/profile")
    assert response.status_code == 401


def test_new_endpoints_reject_wrong_auth(database) -> None:
    client = _client(database)
    wrong = {"Authorization": "Bearer wrong-token"}
    assert client.get("/api/v1/system/profile", headers=wrong).status_code == 401
    assert client.get("/api/v1/system/doctor", headers=wrong).status_code == 401
    assert client.post("/api/v1/runtimes/refresh", headers=wrong).status_code == 401
    assert (
        client.post(
            "/api/v1/models/pull",
            json={"runtime_id": 1, "model_reference": "x", "user_approved": True},
            headers=wrong,
        ).status_code
        == 401
    )


def test_system_profile_returns_real_hardware_shape(database) -> None:
    client = _client(database)
    response = client.get("/api/v1/system/profile", headers=_headers())
    assert response.status_code == 200
    body = response.json()
    assert "os" in body
    assert "arch" in body
    assert "cpu" in body
    assert "memory" in body
    assert "disk" in body
    assert "accelerators" in body
    assert Path(body["disk"]["path"]).is_dir()


def test_system_doctor_covers_runtime_storage_and_training(database) -> None:
    client = _client(database)
    response = client.get("/api/v1/system/doctor", headers=_headers())
    assert response.status_code == 200
    body = response.json()
    check_ids = [check["check_id"] for check in body["checks"]]
    for required in (
        "platform",
        "memory-disk",
        "sqlite",
        "runtimes",
        "storage-roots",
        "optional-dependencies",
        "loopback-auth",
    ):
        assert required in check_ids, f"doctor missing check {required}"
    sqlite_check = next(check for check in body["checks"] if check["check_id"] == "sqlite")
    assert sqlite_check["status"] == "pass"
    runtime_check = next(check for check in body["checks"] if check["check_id"] == "runtimes")
    assert runtime_check["status"] == "pass"


def test_runtime_refresh_persists_discovery(database) -> None:
    client = _client(database)
    response = client.post("/api/v1/runtimes/refresh", headers=_headers())
    assert response.status_code == 200
    job = response.json()
    assert job["kind"] == "runtime_refresh"
    assert job["status"] == "SUCCEEDED"

    runtimes = client.get("/api/v1/runtimes", headers=_headers()).json()
    assert len(runtimes) == 1
    assert runtimes[0]["endpoint"] == "http://127.0.0.1:11434"
    assert runtimes[0]["source"] == "auto"

    models = client.get("/api/v1/models", headers=_headers()).json()
    assert len(models) == 1
    assert models[0]["model_id"] == "ollama-local:default"
    assert models[0]["digest"] == "sha256:abc"
    assert models[0]["runtime_id"] == runtimes[0]["id"]


def test_refresh_keeps_shared_port_kinds_separate(database) -> None:
    descriptors = [
        _descriptor(
            kind=RuntimeKind.LLAMA_CPP,
            runtime_id="llama-cpp-local",
            endpoint="http://127.0.0.1:8080",
            models=[_model("llama-model", runtime_id="llama-cpp-local")],
        ),
        _descriptor(
            kind=RuntimeKind.MLX_LM,
            runtime_id="mlx-lm-local",
            endpoint="http://127.0.0.1:8080",
            models=[_model("mlx-model", runtime_id="mlx-lm-local")],
        ),
    ]
    registry = FakeRegistry(descriptors)
    client = _client(database, registry)
    assert client.post("/api/v1/runtimes/refresh", headers=_headers()).status_code == 200
    first = client.get("/api/v1/runtimes", headers=_headers()).json()
    assert len(first) == 2
    assert {item["kind"] for item in first} == {"llama.cpp", "mlx-lm"}

    assert client.post("/api/v1/runtimes/refresh", headers=_headers()).status_code == 200
    second = client.get("/api/v1/runtimes", headers=_headers()).json()
    assert len(second) == 2
    assert {item["id"] for item in second} == {item["id"] for item in first}


def test_online_refresh_prunes_disappeared_models(database) -> None:
    registry = FakeRegistry([_descriptor(models=[_model("keep-me")])])
    client = _client(database, registry)
    assert client.post("/api/v1/runtimes/refresh", headers=_headers()).status_code == 200
    model_ids = [
        item["model_id"] for item in client.get("/api/v1/models", headers=_headers()).json()
    ]
    assert model_ids == ["keep-me"]

    registry.descriptors = [_descriptor(models=[_model("replacement")])]
    assert client.post("/api/v1/runtimes/refresh", headers=_headers()).status_code == 200
    model_ids = [
        item["model_id"] for item in client.get("/api/v1/models", headers=_headers()).json()
    ]
    assert model_ids == ["replacement"]


def test_offline_refresh_never_prunes_models(database) -> None:
    registry = FakeRegistry([_descriptor(models=[_model("persist-me")])])
    client = _client(database, registry)
    assert client.post("/api/v1/runtimes/refresh", headers=_headers()).status_code == 200

    registry.descriptors = [_descriptor(status=RuntimeStatus.OFFLINE, registered=False, models=[])]
    assert client.post("/api/v1/runtimes/refresh", headers=_headers()).status_code == 200
    model_ids = [
        item["model_id"] for item in client.get("/api/v1/models", headers=_headers()).json()
    ]
    assert model_ids == ["persist-me"]


def test_failed_refresh_persists_failed_job_without_partial_discovery(database) -> None:
    client = _client(database, RaisingRegistry())
    response = client.post("/api/v1/runtimes/refresh", headers=_headers())
    assert response.status_code == 200
    job = response.json()
    assert job["kind"] == "runtime_refresh"
    assert job["status"] == "FAILED"
    assert job["error_json"]["code"] == "RUNTIME_REFRESH_FAILED"
    assert job["error_json"]["recoverable"] is True
    assert job["error_json"]["actions"] == ["retry_refresh"]

    fetched = client.get(f"/api/v1/jobs/{job['id']}", headers=_headers())
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "FAILED"
    assert client.get("/api/v1/runtimes", headers=_headers()).json() == []
    assert client.get("/api/v1/models", headers=_headers()).json() == []


def _seed_ollama(
    client: TestClient,
    database,
    *,
    endpoint: str = "http://127.0.0.1:11434",
) -> int:
    created = client.post(
        "/api/v1/runtimes/manual",
        json={"kind": "ollama", "endpoint": endpoint},
        headers=_headers(),
    )
    assert created.status_code == 201
    runtime_id = created.json()["id"]
    with UnitOfWork(database.session_factory) as uow:
        runtime = uow.runtimes.get(runtime_id)
        assert runtime is not None
        runtime.status = RuntimeStatus.ONLINE
    return runtime_id


def test_model_pull_records_persisted_job(database) -> None:
    client = _client(database)
    runtime_id = _seed_ollama(client, database)

    pull = client.post(
        "/api/v1/models/pull",
        json={
            "runtime_id": runtime_id,
            "model_reference": "qwen2:1.5b",
            "user_approved": True,
            "expected_size_bytes": 1_000_000_000,
            "deadline_seconds": 60.0,
        },
        headers=_headers(),
    )
    assert pull.status_code == 201
    job = pull.json()
    assert job["kind"] == "model_pull"
    assert job["status"] == "PENDING"
    assert job["phase"] == "queued"
    assert job["message"] == "qwen2:1.5b"
    assert job["error_json"]["code"] == "ACQUISITION_QUEUED"
    assert "request" not in job["error_json"]
    assert "plan" not in job["error_json"]
    assert "runtime_endpoint" not in job["error_json"]
    assert "http://127.0.0.1:11434" not in json.dumps(job)
    assert job["error_json"]["model_reference"] == "qwen2:1.5b"
    assert job["error_json"]["user_approved"] is True
    assert job["error_json"]["expected_size_bytes"] == 1_000_000_000
    assert job["error_json"]["deadline_seconds"] == 60.0
    assert len(job["error_json"]["runtime_identity"]) == 64

    fetched = client.get(f"/api/v1/jobs/{job['id']}", headers=_headers())
    assert fetched.status_code == 200
    assert fetched.json()["id"] == job["id"]


def test_model_pull_requires_auth(database) -> None:
    client = _client(database)
    response = client.post(
        "/api/v1/models/pull",
        json={"runtime_id": 1, "model_reference": "x", "user_approved": True},
    )
    assert response.status_code == 401


def test_model_pull_rejects_missing_runtime(database) -> None:
    client = _client(database)
    response = client.post(
        "/api/v1/models/pull",
        json={"runtime_id": 999999, "model_reference": "qwen2:1.5b", "user_approved": True},
        headers=_headers(),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RUNTIME_NOT_FOUND"


def test_model_pull_rejects_non_ollama_runtime(database) -> None:
    client = _client(database)
    created = client.post(
        "/api/v1/runtimes/manual",
        json={"kind": "openai-compatible", "endpoint": "http://127.0.0.1:8080/v1"},
        headers=_headers(),
    )
    runtime_id = created.json()["id"]
    response = client.post(
        "/api/v1/models/pull",
        json={"runtime_id": runtime_id, "model_reference": "qwen2:1.5b", "user_approved": True},
        headers=_headers(),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UNSUPPORTED_RUNTIME_PULL"


def test_model_pull_requires_explicit_approval(database) -> None:
    client = _client(database)
    runtime_id = _seed_ollama(client, database)
    response = client.post(
        "/api/v1/models/pull",
        json={"runtime_id": runtime_id, "model_reference": "qwen2:1.5b", "user_approved": False},
        headers=_headers(),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "USER_APPROVAL_REQUIRED"
    assert response.json()["error"]["actions"] == ["confirm_model_download"]


def test_model_pull_rejects_missing_approval(database) -> None:
    client = _client(database)
    runtime_id = _seed_ollama(client, database)
    response = client.post(
        "/api/v1/models/pull",
        json={"runtime_id": runtime_id, "model_reference": "qwen2:1.5b"},
        headers=_headers(),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_model_pull_rejects_coerced_and_extra_fields(database) -> None:
    client = _client(database)
    runtime_id = _seed_ollama(client, database)
    coerced = client.post(
        "/api/v1/models/pull",
        json={
            "runtime_id": runtime_id,
            "model_reference": "qwen2:1.5b",
            "user_approved": "true",
        },
        headers=_headers(),
    )
    assert coerced.status_code == 422
    assert coerced.json()["error"]["code"] == "VALIDATION_ERROR"

    extra = client.post(
        "/api/v1/models/pull",
        json={
            "runtime_id": runtime_id,
            "model_reference": "qwen2:1.5b",
            "user_approved": True,
            "proxy": True,
        },
        headers=_headers(),
    )
    assert extra.status_code == 422
    assert extra.json()["error"]["code"] == "VALIDATION_ERROR"


def test_model_pull_rejects_remote_endpoint(database) -> None:
    client = _client(database)
    runtime_id = _seed_ollama(client, database, endpoint="http://example.com:11434")
    response = client.post(
        "/api/v1/models/pull",
        json={"runtime_id": runtime_id, "model_reference": "qwen2:1.5b", "user_approved": True},
        headers=_headers(),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_ENDPOINT"


def test_model_pull_rejects_path_endpoint(database) -> None:
    client = _client(database)
    runtime_id = _seed_ollama(client, database, endpoint="http://127.0.0.1:11434/v1")
    response = client.post(
        "/api/v1/models/pull",
        json={"runtime_id": runtime_id, "model_reference": "qwen2:1.5b", "user_approved": True},
        headers=_headers(),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_ENDPOINT"


def test_model_pull_bounds_model_reference(database) -> None:
    client = _client(database)
    runtime_id = _seed_ollama(client, database)
    response = client.post(
        "/api/v1/models/pull",
        json={
            "runtime_id": runtime_id,
            "model_reference": "x" * 300,
            "user_approved": True,
        },
        headers=_headers(),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_model_detail_supports_slash_in_key_without_shadowing_pull(database) -> None:
    registry = FakeRegistry([_descriptor(models=[_model("qwen2/1.5b")])])
    client = _client(database, registry)
    assert client.post("/api/v1/runtimes/refresh", headers=_headers()).status_code == 200
    runtime = client.get("/api/v1/runtimes", headers=_headers()).json()[0]
    key = f"{runtime['id']}:qwen2/1.5b"

    detail = client.get(f"/api/v1/models/{key}", headers=_headers())
    assert detail.status_code == 200
    assert detail.json()["model_id"] == "qwen2/1.5b"

    runtime_id = _seed_ollama(client, database)
    pull = client.post(
        "/api/v1/models/pull",
        json={
            "runtime_id": runtime_id,
            "model_reference": "qwen2:1.5b",
            "user_approved": True,
            "expected_size_bytes": 100,
        },
        headers=_headers(),
    )
    assert pull.status_code == 201
