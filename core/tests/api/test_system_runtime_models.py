"""Focused tests for system profile/doctor, runtime refresh, and model pull."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from zana_core.domain.enums import (
    ModelIdentityStrength,
    RuntimeKind,
    RuntimeSource,
    RuntimeStatus,
)
from zana_core.main import create_app
from zana_core.runtimes.base import ModelDescriptor, RuntimeDescriptor
from zana_core.runtimes.registry import RuntimeProbeRegistry


def _descriptor() -> RuntimeDescriptor:
    return RuntimeDescriptor(
        runtime_id="ollama-local",
        kind=RuntimeKind.OLLAMA,
        endpoint="http://127.0.0.1:11434",
        source=RuntimeSource.AUTO,
        status=RuntimeStatus.ONLINE,
        registered=True,
        server_running=True,
        installed=True,
        installed_not_running=False,
        identified_vendor="ollama",
        evidence=["/api/tags 200"],
        warnings=[],
        error=None,
        models=[
            ModelDescriptor(
                runtime_id="ollama-local",
                model_id="qwen2:1.5b",
                display_name="qwen2:1.5b",
                digest="sha256:abc",
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
        ],
        last_seen_at=datetime.now(UTC),
    )


class FakeRegistry(RuntimeProbeRegistry):
    """Registry substitute returning protocol descriptors without network I/O."""

    def probe(self, targets):  # noqa: ANN001
        return [_descriptor()]


def _app(database, token="test-token-abc123"):
    return create_app(
        token=token,
        database_path=database.path,
        runtime_registry=FakeRegistry(),
    )


def _client(database) -> tuple[TestClient, dict[str, str]]:
    client = TestClient(_app(database))
    return client, {"Authorization": "Bearer test-token-abc123"}


def test_system_profile_requires_auth(database) -> None:
    client = TestClient(_app(database))
    response = client.get("/api/v1/system/profile")
    assert response.status_code == 401


def test_system_profile_returns_real_hardware_shape(database) -> None:
    client, headers = _client(database)
    response = client.get("/api/v1/system/profile", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert "os" in body
    assert "arch" in body
    assert "cpu" in body
    assert "memory" in body
    assert "disk" in body
    assert "accelerators" in body
    assert Path(body["disk"]["path"]).is_dir()


def test_system_doctor_returns_diagnostic_report(database) -> None:
    client, headers = _client(database)
    response = client.get("/api/v1/system/doctor", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert "aggregate_health" in body
    assert "checks" in body
    check_ids = [check["check_id"] for check in body["checks"]]
    assert "sqlite" in check_ids
    sqlite_check = next(check for check in body["checks"] if check["check_id"] == "sqlite")
    assert sqlite_check["status"] == "pass"


def test_runtime_refresh_persists_discovery(database) -> None:
    client, headers = _client(database)
    response = client.post("/api/v1/runtimes/refresh", headers=headers)
    assert response.status_code == 200
    job = response.json()
    assert job["kind"] == "runtime_refresh"
    assert job["status"] == "SUCCEEDED"

    runtimes = client.get("/api/v1/runtimes", headers=headers).json()
    assert len(runtimes) == 1
    assert runtimes[0]["endpoint"] == "http://127.0.0.1:11434"
    assert runtimes[0]["source"] == "auto"

    models = client.get("/api/v1/models", headers=headers).json()
    assert len(models) == 1
    model = models[0]
    assert model["model_id"] == "qwen2:1.5b"
    assert model["digest"] == "sha256:abc"
    assert model["runtime_id"] == runtimes[0]["id"]


def test_model_pull_records_persisted_job(database) -> None:
    client, headers = _client(database)
    created = client.post(
        "/api/v1/runtimes/manual",
        json={"kind": "ollama", "endpoint": "http://127.0.0.1:11434"},
        headers=headers,
    )
    assert created.status_code == 201
    runtime_id = created.json()["id"]

    pull = client.post(
        "/api/v1/models/pull",
        json={"runtime_id": runtime_id, "model_reference": "qwen2:1.5b"},
        headers=headers,
    )
    assert pull.status_code == 201
    job = pull.json()
    assert job["kind"] == "model_pull"
    assert job["status"] == "PENDING"
    assert job["phase"] == "queued"
    assert job["message"] == "qwen2:1.5b"
    assert job["error_json"]["code"] == "ACQUISITION_QUEUED"
    assert job["error_json"]["plan"]["model_reference"] == "qwen2:1.5b"

    fetched = client.get(f"/api/v1/jobs/{job['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["id"] == job["id"]


def test_model_pull_requires_auth(database) -> None:
    client = TestClient(_app(database))
    response = client.post("/api/v1/models/pull", json={"runtime_id": 1, "model_reference": "x"})
    assert response.status_code == 401


def test_model_pull_rejects_missing_runtime(database) -> None:
    client, headers = _client(database)
    response = client.post(
        "/api/v1/models/pull",
        json={"runtime_id": 999999, "model_reference": "qwen2:1.5b"},
        headers=headers,
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RUNTIME_NOT_FOUND"


def test_model_pull_rejects_non_ollama_runtime(database) -> None:
    client, headers = _client(database)
    created = client.post(
        "/api/v1/runtimes/manual",
        json={"kind": "openai-compatible", "endpoint": "http://127.0.0.1:8080/v1"},
        headers=headers,
    )
    assert created.status_code == 201
    runtime_id = created.json()["id"]
    response = client.post(
        "/api/v1/models/pull",
        json={"runtime_id": runtime_id, "model_reference": "qwen2:1.5b"},
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UNSUPPORTED_RUNTIME_PULL"


def test_model_pull_bounds_model_reference(database) -> None:
    client, headers = _client(database)
    created = client.post(
        "/api/v1/runtimes/manual",
        json={"kind": "ollama", "endpoint": "http://127.0.0.1:11434"},
        headers=headers,
    )
    runtime_id = created.json()["id"]
    response = client.post(
        "/api/v1/models/pull",
        json={"runtime_id": runtime_id, "model_reference": "x" * 300},
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
