"""Focused API tests for the isolated resource and observability routers."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from zana_core.api.observability import router as observability_router
from zana_core.api.resources import router as resources_router
from zana_core.main import create_app
from zana_core.observability.events import Event, EventKind, Severity
from zana_core.observability.registry import ObservabilityRegistry
from zana_core.observability.sinks import BoundedMemorySink, LocalJsonlSink
from zana_core.resources.governor import ResourceGovernor
from zana_core.resources.models import (
    OperationCategory,
    OperationRequest,
    PlatformLabel,
    ResourcePolicy,
    ResourceSnapshot,
)
from zana_core.resources.service import ResourceService
from zana_core.resources.snapshot import SnapshotProvider

AUTH = {"Authorization": "Bearer test-token-abc123"}

RESOURCE_PATHS = [
    ("GET", "/api/v1/resources/snapshot"),
    ("POST", "/api/v1/resources/snapshot/refresh"),
    ("GET", "/api/v1/resources/policy"),
    ("GET", "/api/v1/resources/leases"),
    ("GET", "/api/v1/resources/usage"),
    ("GET", "/api/v1/observability/events"),
    ("GET", "/api/v1/observability/health"),
]


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


class FixedSnapshotProvider:
    def __init__(self, snap: ResourceSnapshot) -> None:
        self._snap = snap
        self.calls = 0

    def capture(self) -> ResourceSnapshot:
        self.calls += 1
        return self._snap


class FailingSnapshotProvider:
    def capture(self) -> ResourceSnapshot:
        raise RuntimeError("injected probe boom")


def _snapshot(**overrides: object) -> ResourceSnapshot:
    values: dict[str, object] = {
        "revision": 0,
        "platform": PlatformLabel.MACOS,
        "os_name": "test",
        "arch": "arm64",
        "logical_cores": 12,
        "memory_total_bytes": 16 << 30,
        "memory_available_bytes": 12 << 30,
        "disk_path": "/private/tmp/zana",
        "disk_free_bytes": 100 << 30,
        "probe_error": None,
        "notes": (),
    }
    values.update(overrides)
    return ResourceSnapshot(**values)  # type: ignore[arg-type]


def _request(request_id: str) -> OperationRequest:
    return OperationRequest(
        id=request_id,
        category=OperationCategory.TINY,
        name="tiny-operation",
        required_memory_bytes=1 << 20,
        required_disk_bytes=2 << 20,
    )


def _event(index: int, **payload: object) -> Event:
    return Event(
        kind=EventKind.SYSTEM,
        severity=Severity.INFO,
        message=f"event {index}",
        operation_id=f"op-{index}",
        payload=payload,
    )


def _client(
    database,
    *,
    service: ResourceService | None = None,
    registry: ObservabilityRegistry | None = None,
) -> TestClient:
    app = create_app(token="test-token-abc123", database_path=database.path)
    if service is not None:
        app.state.resource_service = service
    if registry is not None:
        app.state.observability_registry = registry
    app.include_router(resources_router)
    app.include_router(observability_router)
    return TestClient(app)


def _service(
    *,
    provider: SnapshotProvider | None = None,
    snap: ResourceSnapshot | None = None,
    governor: ResourceGovernor | None = None,
    now: Clock | None = None,
) -> ResourceService:
    if provider is None:
        provider = FixedSnapshotProvider(snap or _snapshot())
    return ResourceService(governor=governor, provider=provider, now=now)


def _registry() -> ObservabilityRegistry:
    return ObservabilityRegistry(memory_sink=BoundedMemorySink(max_events=100, max_bytes=1_000_000))


def test_all_resource_and_observability_endpoints_require_auth(database) -> None:  # noqa: ANN001
    client = _client(database, service=_service(), registry=_registry())
    for method, path in RESOURCE_PATHS:
        missing = client.request(method, path)
        assert missing.status_code == 401, f"{method} {path} missing token"
        assert missing.json()["error"]["code"] == "UNAUTHORIZED"
        wrong = client.request(method, path, headers={"Authorization": "Bearer wrong-token"})
        assert wrong.status_code == 401, f"{method} {path} wrong token"


def test_unconfigured_services_return_canonical_503(database) -> None:  # noqa: ANN001
    client = _client(database)
    resources = client.get("/api/v1/resources/snapshot", headers=AUTH)
    assert resources.status_code == 503
    assert resources.json()["error"]["code"] == "RESOURCES_SERVICE_UNAVAILABLE"
    observability = client.get("/api/v1/observability/health", headers=AUTH)
    assert observability.status_code == 503
    assert observability.json()["error"]["code"] == "OBSERVABILITY_SERVICE_UNAVAILABLE"


def test_snapshot_redacts_paths_and_exposes_real_facts(database) -> None:  # noqa: ANN001
    snap = _snapshot(disk_path="/private/tmp/zana-secret-root")
    client = _client(database, service=_service(snap=snap))
    body = client.get("/api/v1/resources/snapshot", headers=AUTH).json()
    assert body["revision"] == 0
    assert body["memory_total_bytes"] == 16 << 30
    assert body["memory_available_bytes"] == 12 << 30
    assert body["probe_status"] == "ok"
    assert body["probe_error_code"] is None
    assert body["fresh"] is True
    assert body["disk_path"] == "zana-secret-root"
    assert "/private/tmp/zana-secret-root" not in json.dumps(body)


def test_snapshot_freshness_and_explicit_refresh(database) -> None:  # noqa: ANN001
    clock = Clock()
    client = _client(database, service=_service(now=clock))
    first = client.get("/api/v1/resources/snapshot", headers=AUTH).json()
    assert first["fresh"] is True
    clock.advance(31)
    stale = client.get("/api/v1/resources/snapshot", headers=AUTH).json()
    assert stale["fresh"] is False
    assert stale["age_seconds"] >= 31
    refreshed = client.post("/api/v1/resources/snapshot/refresh", headers=AUTH).json()
    assert refreshed["fresh"] is True
    assert refreshed["revision"] == 1


def test_snapshot_unknown_state_never_leaks_probe_error(database) -> None:  # noqa: ANN001
    client = _client(database, service=_service(provider=FailingSnapshotProvider()))
    body = client.get("/api/v1/resources/snapshot", headers=AUTH).json()
    assert body["probe_status"] == "unavailable"
    assert body["probe_error_code"] == "SNAPSHOT_PROVIDER_UNAVAILABLE"
    assert body["memory_total_bytes"] is None
    assert "boom" not in json.dumps(body)


def test_policy_leases_and_usage_are_typed(database) -> None:  # noqa: ANN001
    governor = ResourceGovernor(ResourcePolicy(), FixedSnapshotProvider(_snapshot()))
    service = _service(governor=governor)
    client = _client(database, service=service)
    policy = client.get("/api/v1/resources/policy", headers=AUTH).json()
    assert policy["revision"] == 1
    categories = {item["category"]: item for item in policy["categories"]}
    assert "training" in categories
    assert categories["training"]["max_concurrency"] == 1
    assert categories["tiny"]["max_concurrency"] == 16

    decision = service.admit(_request("api-req"))
    assert decision.lease is not None
    leases = client.get("/api/v1/resources/leases", headers=AUTH).json()
    assert [lease["request_id"] for lease in leases] == ["api-req"]
    assert leases[0]["active"] is True
    service.release(decision.lease.token)

    usage = client.get("/api/v1/resources/usage", headers=AUTH).json()
    assert usage["count"] == 2
    assert usage["total_available"] == 2
    assert [record["released"] for record in usage["items"]] == [True, False]


def test_usage_pagination_cursor_walks_backward(database) -> None:  # noqa: ANN001
    governor = ResourceGovernor(ResourcePolicy(), FixedSnapshotProvider(_snapshot()))
    service = _service(governor=governor)
    for index in range(1, 6):
        decision = service.admit(_request(f"r-{index}"))
        assert decision.lease is not None
        service.release(decision.lease.token)
    client = _client(database, service=service)
    collected: list[int] = []
    cursor: int | None = None
    for _ in range(6):
        params: dict[str, object] = {"limit": 2}
        if cursor is not None:
            params["before_sequence"] = cursor
        page = client.get("/api/v1/resources/usage", params=params, headers=AUTH).json()
        collected.extend(item["sequence"] for item in page["items"])
        if page["next_cursor"] is None:
            break
        cursor = page["next_cursor"]
    assert collected == list(range(10, 0, -1))


def test_event_pagination_and_redaction(database) -> None:  # noqa: ANN001
    registry = _registry()
    for index in range(1, 6):
        registry.write(_event(index, password=f"secret-{index}", path="/private/tmp/secret.txt"))
    client = _client(database, registry=registry)
    page = client.get(
        "/api/v1/observability/events",
        params={"limit": 2},
        headers=AUTH,
    ).json()
    assert page["count"] == 2
    assert page["truncated"] is True
    assert page["next_cursor"] == 4
    assert page["total_available"] == 5
    assert page["retention_dropped"] == 0
    assert [item["sequence"] for item in page["items"]] == [5, 4]
    body_text = json.dumps(page)
    assert "secret-1" not in body_text
    assert "/private/tmp/secret.txt" not in body_text
    for item in page["items"]:
        assert item["kind"] == "system"
        assert item["severity"] == "info"
        assert item["payload"]["password"] == "***"
        assert "public" not in item["payload"]


def test_event_retention_is_bounded_and_explicit(database) -> None:  # noqa: ANN001
    registry = ObservabilityRegistry(
        memory_sink=BoundedMemorySink(max_events=100, max_bytes=1_000_000),
        max_retained_events=3,
    )
    for index in range(1, 6):
        registry.write(_event(index))
    client = _client(database, registry=registry)
    health = client.get("/api/v1/observability/health", headers=AUTH).json()
    assert health["retained_events"] == 3
    assert health["retention_dropped"] == 2
    page = client.get("/api/v1/observability/events", headers=AUTH).json()
    assert page["total_available"] == 3
    assert [item["sequence"] for item in page["items"]] == [5, 4, 3]


def test_sink_health_redacts_paths_and_telemetry_is_off(
    database,
    tmp_path,
) -> None:  # noqa: ANN001
    jsonl = LocalJsonlSink(log_root=tmp_path, filename="events.jsonl", max_bytes=4096)
    registry = ObservabilityRegistry(
        memory_sink=BoundedMemorySink(max_events=10, max_bytes=100_000),
        jsonl_sink=jsonl,
    )
    registry.write(_event(1))
    client = _client(database, registry=registry)
    body = client.get("/api/v1/observability/health", headers=AUTH).json()
    assert body["telemetry_enabled"] is False
    assert body["remote_transport"] == "none"
    assert body["mode"] == "local_memory_jsonl"
    assert body["memory"]["present"] is True
    assert body["memory"]["stats"]["events_written"] == 1
    assert body["jsonl"]["available"] is True
    assert body["jsonl"]["stats"]["events_written"] == 1
    assert body["total"]["events_written"] == 2
    assert str(tmp_path) not in json.dumps(body)
    assert body["jsonl"]["log_root"] != str(tmp_path)


def test_jsonl_unsupported_state_is_explicit(database) -> None:  # noqa: ANN001
    registry = ObservabilityRegistry(
        memory_sink=BoundedMemorySink(max_events=10, max_bytes=100_000),
        jsonl_error="PLATFORM_UNSUPPORTED",
    )
    client = _client(database, registry=registry)
    body = client.get("/api/v1/observability/health", headers=AUTH).json()
    assert body["jsonl"]["present"] is False
    assert body["jsonl"]["available"] is False
    assert body["jsonl"]["reason"] == "PLATFORM_UNSUPPORTED"
    assert body["jsonl"]["log_root"] is None
    assert body["mode"] == "local_memory"


def test_resource_and_event_pagination_reject_invalid_bounds(database) -> None:  # noqa: ANN001
    client = _client(database, service=_service(), registry=_registry())
    assert (
        client.get("/api/v1/resources/usage", params={"limit": 0}, headers=AUTH).status_code == 422
    )
    assert (
        client.get(
            "/api/v1/resources/usage",
            params={"before_sequence": -1},
            headers=AUTH,
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/v1/observability/events",
            params={"limit": 0},
            headers=AUTH,
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/v1/observability/events",
            params={"before_sequence": -1},
            headers=AUTH,
        ).status_code
        == 422
    )
