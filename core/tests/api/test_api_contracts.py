"""Authenticated API registration, job SSE, and real persistence tests."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from zana_core.db.models import Capability, Model, Runtime
from zana_core.db.unit_of_work import UnitOfWork
from zana_core.domain.enums import (
    JobKind,
    JobStatus,
    ModelIdentityStrength,
    RuntimeKind,
    RuntimeSource,
    RuntimeStatus,
)
from zana_core.jobs.services import JobService
from zana_core.main import create_app

from .test_system_runtime_models import FakeRegistry


def _parse_sse(text: str) -> list[dict[str, object]]:
    """Parse canonical SSE framing into typed event records for tests."""
    events: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for raw_line in text.split("\n"):
        if raw_line == "":
            if current is not None:
                events.append(current)
                current = None
            continue
        if raw_line.startswith(":"):
            continue
        field, _, value = raw_line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "id":
            if current is None:
                current = {}
            current["id"] = value
        elif field == "event":
            if current is None:
                current = {}
            current["event"] = value
        elif field == "data":
            if current is None:
                current = {}
            current.setdefault("data", []).append(value)
    if current is not None:
        events.append(current)
    return events


AUTH_PROTECTED_PATHS = [
    ("GET", "/api/v1/runtimes"),
    ("POST", "/api/v1/runtimes/refresh"),
    ("POST", "/api/v1/runtimes/manual"),
    ("GET", "/api/v1/models"),
    ("POST", "/api/v1/models/pull"),
    ("GET", "/api/v1/models/ollama:example"),
    ("GET", "/api/v1/capabilities"),
    ("POST", "/api/v1/capabilities"),
    ("POST", "/api/v1/builds/analyze"),
    ("GET", "/api/v1/builds/1"),
    ("GET", "/api/v1/jobs/1"),
    ("GET", "/api/v1/jobs/1/events"),
    ("GET", "/api/v1/images"),
    ("GET", "/api/v1/images/sha256:abc"),
    ("GET", "/api/v1/system/profile"),
    ("GET", "/api/v1/system/doctor"),
]


class TestAuthentication:
    def test_all_registered_endpoints_require_auth(
        self,
        client: TestClient,
    ) -> None:
        for method, path in AUTH_PROTECTED_PATHS:
            response = client.request(method, path)
            assert response.status_code == 401, f"{method} {path} must be authenticated"
            body = response.json()
            assert body["error"]["code"] == "UNAUTHORIZED"


class TestRuntimeApi:
    def test_manual_runtime_lifecycle(
        self,
        client: TestClient,
        auth_header: dict[str, str],
    ) -> None:
        payload = {
            "kind": "openai-compatible",
            "endpoint": "http://127.0.0.1:8080/v1",
            "metadata_json": {"manual": True},
        }
        created = client.post("/api/v1/runtimes/manual", json=payload, headers=auth_header)
        assert created.status_code == 201
        runtime_id = created.json()["id"]
        assert created.json()["source"] == "manual"

        listed = client.get("/api/v1/runtimes", headers=auth_header)
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()] == [runtime_id]

        deleted = client.delete(f"/api/v1/runtimes/{runtime_id}", headers=auth_header)
        assert deleted.status_code == 204
        missing = client.delete(f"/api/v1/runtimes/{runtime_id}", headers=auth_header)
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "RUNTIME_NOT_FOUND"

    def test_manual_runtime_rejects_bad_endpoints(
        self,
        client: TestClient,
        auth_header: dict[str, str],
    ) -> None:
        response = client.post(
            "/api/v1/runtimes/manual",
            json={"kind": "ollama", "endpoint": "https://user:pass@example.com/v1"},
            headers=auth_header,
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "ENDPOINT_CREDENTIALS_NOT_ALLOWED"

    def test_duplicate_manual_endpoint_conflicts(
        self,
        client: TestClient,
        auth_header: dict[str, str],
    ) -> None:
        payload = {"kind": "ollama", "endpoint": "http://127.0.0.1:11434"}
        created = client.post("/api/v1/runtimes/manual", json=payload, headers=auth_header)
        assert created.status_code == 201
        duplicate = client.post("/api/v1/runtimes/manual", json=payload, headers=auth_header)
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "RUNTIME_ALREADY_EXISTS"


class TestCapabilityApi:
    def test_capability_lifecycle(
        self,
        client: TestClient,
        auth_header: dict[str, str],
    ) -> None:
        created = client.post(
            "/api/v1/capabilities",
            json={
                "name": "math-tutor",
                "version": "0.1.0",
                "manifest_json": {"kind": "ZanaCapability", "schemaVersion": 1},
            },
            headers=auth_header,
        )
        assert created.status_code == 201
        capability_id = created.json()["id"]
        assert created.json()["manifest_json"]["kind"] == "ZanaCapability"

        listed = client.get("/api/v1/capabilities", headers=auth_header)
        assert [item["id"] for item in listed.json()] == [capability_id]

        updated = client.put(
            f"/api/v1/capabilities/{capability_id}",
            json={"name": "math-tutor-v2"},
            headers=auth_header,
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "math-tutor-v2"

        missing = client.get("/api/v1/capabilities/999999", headers=auth_header)
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "CAPABILITY_NOT_FOUND"


class TestBuildApi:
    def test_analyze_requires_real_capability_and_model(
        self,
        client: TestClient,
        auth_header: dict[str, str],
    ) -> None:
        missing_capability = client.post(
            "/api/v1/builds/analyze",
            json={"capability_id": 999999, "model_key": "ollama:missing"},
            headers=auth_header,
        )
        assert missing_capability.status_code == 404
        assert missing_capability.json()["error"]["code"] == "CAPABILITY_NOT_FOUND"

    def test_analyze_records_draft_job_and_cancel(
        self,
        client: TestClient,
        auth_header: dict[str, str],
        uow: UnitOfWork,
    ) -> None:
        self._seed_capability_and_model(uow)
        created = client.post(
            "/api/v1/builds/analyze",
            json={
                "capability_id": 1,
                "model_key": "ollama:example",
                "policy_json": {"strategy": "RAG_ONLY"},
            },
            headers=auth_header,
        )
        assert created.status_code == 201
        build_id = created.json()["id"]
        assert created.json()["status"] == "DRAFT"
        assert created.json()["error_json"]["code"] == "ANALYSIS_NOT_STARTED"

        fetched = client.get(f"/api/v1/builds/{build_id}", headers=auth_header)
        assert fetched.status_code == 200
        assert fetched.json()["status"] == "DRAFT"

        cancelled = client.post(f"/api/v1/builds/{build_id}/cancel", headers=auth_header)
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "CANCELLED"

        again = client.post(f"/api/v1/builds/{build_id}/cancel", headers=auth_header)
        assert again.status_code == 409
        assert again.json()["error"]["code"] == "INVALID_TRANSITION"

    @staticmethod
    def _seed_capability_and_model(uow: UnitOfWork) -> None:
        runtime = uow.runtimes.add(
            Runtime(
                kind=RuntimeKind.OLLAMA,
                endpoint="http://127.0.0.1:11434",
                source=RuntimeSource.AUTO,
                status=RuntimeStatus.ONLINE,
            )
        )
        uow.session.flush()
        uow.models.add(
            Model(
                key="ollama:example",
                runtime_id=runtime.id,
                model_id="example",
                digest="sha256:base",
                capabilities_json=["completion"],
                identity_strength=ModelIdentityStrength.EXACT_DIGEST,
            )
        )
        uow.capabilities.add(Capability(name="math-tutor", version="0.1.0"))
        uow.commit()


class TestJobsApi:
    def test_job_and_events_are_persisted(
        self,
        client: TestClient,
        auth_header: dict[str, str],
        uow: UnitOfWork,
    ) -> None:
        service = JobService(uow)
        job = service.create_job(JobKind.MODEL_PULL, phase="queued")
        service.transition_job(job.id, JobStatus.RUNNING)
        uow.commit()

        fetched = client.get(f"/api/v1/jobs/{job.id}", headers=auth_header)
        assert fetched.status_code == 200
        assert fetched.json()["status"] == "RUNNING"

        events = client.get(f"/api/v1/jobs/{job.id}/events", headers=auth_header)
        assert events.status_code == 200
        assert events.headers["content-type"].startswith("text/event-stream")
        assert events.headers["cache-control"] == "no-cache, no-transform"
        assert events.headers["x-accel-buffering"] == "no"
        parsed = _parse_sse(events.text)
        assert [event["event"] for event in parsed] == ["job_created", "job_status"]
        assert [int(event["id"]) for event in parsed] == [1, 2]
        first_data = json.loads(parsed[0]["data"][0])
        assert first_data["job_id"] == job.id
        assert first_data["kind"] == "job_created"

        missing = client.get("/api/v1/jobs/999999", headers=auth_header)
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "JOB_NOT_FOUND"


class TestImagesApi:
    def test_images_are_empty_and_missing_digest_is_404(
        self,
        client: TestClient,
        auth_header: dict[str, str],
    ) -> None:
        listed = client.get("/api/v1/images", headers=auth_header)
        assert listed.status_code == 200
        assert listed.json() == []

        missing = client.get("/api/v1/images/sha256:abc", headers=auth_header)
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "IMAGE_NOT_FOUND"


class TestDoctorApi:
    def test_doctor_reports_bounded_diagnostic_report(
        self,
        database,
        valid_token: str,
        auth_header: dict[str, str],
    ) -> None:
        app = create_app(
            token=valid_token,
            database_path=database.path,
            runtime_registry=FakeRegistry(),
        )
        with TestClient(app) as client:
            response = client.get("/api/v1/system/doctor", headers=auth_header)
        assert response.status_code == 200
        body = response.json()
        assert body["aggregate_health"] in {
            "healthy",
            "pass_with_limited_features",
            "failed",
        }
        assert isinstance(body["checks"], list)
        sqlite_check = next(check for check in body["checks"] if check["check_id"] == "sqlite")
        assert sqlite_check["status"] == "pass"
