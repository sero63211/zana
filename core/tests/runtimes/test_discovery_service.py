"""Focused tests for the shared runtime discovery service."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from zana_core.db.models import Runtime
from zana_core.db.unit_of_work import UnitOfWork
from zana_core.domain.enums import (
    ModelIdentityStrength,
    RuntimeKind,
    RuntimeSource,
    RuntimeStatus,
)
from zana_core.runtimes.base import ModelDescriptor, RuntimeDescriptor
from zana_core.runtimes.discovery_service import (
    RuntimeDiscoveryService,
    RuntimeSnapshot,
)
from zana_core.runtimes.registry import RuntimeProbeRegistry


def _model(model_id: str) -> ModelDescriptor:
    return ModelDescriptor(
        runtime_id="ollama-local",
        model_id=model_id,
        display_name=model_id,
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


def _descriptor(*, models=None) -> RuntimeDescriptor:  # noqa: ANN001
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
        identified_vendor=None,
        evidence=["/api/tags 200"],
        warnings=[],
        error=None,
        models=models if models is not None else [_model("qwen2:1.5b")],
        last_seen_at=datetime.now(UTC),
    )


class RecordingRegistry(RuntimeProbeRegistry):
    def __init__(self, descriptors: list[RuntimeDescriptor] | None = None) -> None:
        super().__init__()
        self.descriptors = descriptors if descriptors is not None else [_descriptor()]
        self.calls: list[list[Any]] = []

    def probe(self, targets: Any) -> list[RuntimeDescriptor]:  # noqa: ANN401
        self.calls.append(list(targets))
        return list(self.descriptors)


def _seed_runtime(session_factory) -> int:
    with UnitOfWork(session_factory) as uow:
        runtime = Runtime(
            kind=RuntimeKind.OLLAMA,
            endpoint="http://127.0.0.1:11434",
            source=RuntimeSource.AUTO,
            status=RuntimeStatus.ONLINE,
        )
        uow.runtimes.add(runtime)
        uow.session.flush()
        return runtime.id


def test_manual_refresh_and_confirm_use_same_discovery_service(session_factory) -> None:
    registry = RecordingRegistry()
    service = RuntimeDiscoveryService(registry)
    runtime_id = _seed_runtime(session_factory)
    job = service.refresh(session_factory)
    assert job is not None
    assert job.status.value == "SUCCEEDED"
    with UnitOfWork(session_factory) as uow:
        runtimes = uow.runtimes.list()
        assert len(runtimes) >= 1
    with UnitOfWork(session_factory) as uow:
        runtime = uow.runtimes.get(runtime_id)
        assert runtime is not None
        snapshot = RuntimeSnapshot(
            id=runtime.id,
            kind=runtime.kind,
            endpoint=runtime.endpoint,
            source=runtime.source,
            status=runtime.status,
        )
    descriptor, model = service.confirm_model(
        session_factory,
        snapshot,
        "qwen2:1.5b",
    )
    assert descriptor is not None
    assert model is not None
    assert model.digest == "sha256:abc"
    assert len(registry.calls) == 2
    with UnitOfWork(session_factory) as uow:
        persisted = uow.models.list_by_runtime(runtime_id)
        assert any(item.model_id == "qwen2:1.5b" for item in persisted)


def test_refresh_probes_with_no_active_uow_transaction(session_factory) -> None:
    observations: list[bool] = []

    class NoTransactionRegistry(RuntimeProbeRegistry):
        def probe(self, targets: Any) -> list[RuntimeDescriptor]:  # noqa: ANN401
            with UnitOfWork(session_factory) as check:
                observations.append(check.session.in_transaction())
            return [_descriptor()]

    service = RuntimeDiscoveryService(NoTransactionRegistry())
    job = service.refresh(session_factory)
    assert job is not None
    assert job.status.value == "SUCCEEDED"
    assert observations == [False]


def test_refresh_sync_failure_records_failed_job_without_partial_rows(
    session_factory,
) -> None:
    class FailingSyncService(RuntimeDiscoveryService):
        def sync(self, uow, descriptors):  # noqa: ANN001, ARG001
            raise RuntimeError("sync boom")

    service = FailingSyncService(RecordingRegistry())
    job = service.refresh(session_factory)
    assert job is not None
    assert job.status.value == "FAILED"
    assert job.error_json["code"] == "RUNTIME_REFRESH_FAILED"
    with UnitOfWork(session_factory) as uow:
        assert uow.runtimes.list() == []
        assert uow.models.list() == []


def test_refresh_target_failure_records_failed_job_without_probe(session_factory) -> None:
    observations: list[bool] = []

    class FailingTargetsService(RuntimeDiscoveryService):
        def targets(self, uow):  # noqa: ANN001
            raise RuntimeError("target secret boom")

    class NoProbeRegistry(RuntimeProbeRegistry):
        def probe(self, targets: Any) -> list[RuntimeDescriptor]:  # noqa: ANN401
            observations.append(True)
            return [_descriptor()]

    service = FailingTargetsService(NoProbeRegistry())
    job = service.refresh(session_factory)
    assert job is not None
    assert job.status.value == "FAILED"
    assert job.error_json["code"] == "RUNTIME_REFRESH_FAILED"
    assert observations == []


def test_refresh_success_transition_failure_records_failed_job(session_factory) -> None:
    class FailingTransitionService(RuntimeDiscoveryService):
        def sync(self, uow, descriptors):  # noqa: ANN001, ARG001
            raise RuntimeError("transition secret boom")

    job = FailingTransitionService(RecordingRegistry()).refresh(session_factory)
    assert job is not None
    assert job.status.value == "FAILED"
    assert job.error_json["code"] == "RUNTIME_REFRESH_FAILED"


def test_confirm_rejects_runtime_identity_change_before_probe(session_factory) -> None:
    registry = RecordingRegistry()
    service = RuntimeDiscoveryService(registry)
    runtime_id = _seed_runtime(session_factory)
    with UnitOfWork(session_factory) as uow:
        runtime = uow.runtimes.get(runtime_id)
        assert runtime is not None
        snapshot = RuntimeSnapshot(
            id=runtime.id,
            kind=runtime.kind,
            endpoint="http://127.0.0.1:9999",
            source=runtime.source,
            status=runtime.status,
        )
    result = service.confirm_model(session_factory, snapshot, "qwen2:1.5b")
    assert result == (None, None)
    assert registry.calls == []


def test_confirm_rejects_disabled_runtime_before_probe(session_factory) -> None:
    registry = RecordingRegistry()
    service = RuntimeDiscoveryService(registry)
    runtime_id = _seed_runtime(session_factory)
    with UnitOfWork(session_factory) as uow:
        runtime = uow.runtimes.get(runtime_id)
        assert runtime is not None
        runtime.status = RuntimeStatus.OFFLINE
        snapshot = RuntimeSnapshot(
            id=runtime.id,
            kind=runtime.kind,
            endpoint=runtime.endpoint,
            source=runtime.source,
            status=runtime.status,
        )
    result = service.confirm_model(session_factory, snapshot, "qwen2:1.5b")
    assert result == (None, None)
    assert registry.calls == []


def test_confirm_missing_model_never_claims_success(session_factory) -> None:
    registry = RecordingRegistry([_descriptor(models=[_model("other")])])
    service = RuntimeDiscoveryService(registry)
    runtime_id = _seed_runtime(session_factory)
    with UnitOfWork(session_factory) as uow:
        runtime = uow.runtimes.get(runtime_id)
        assert runtime is not None
        snapshot = RuntimeSnapshot(
            id=runtime.id,
            kind=runtime.kind,
            endpoint=runtime.endpoint,
            source=runtime.source,
            status=runtime.status,
        )
    descriptor, model = service.confirm_model(session_factory, snapshot, "missing")
    assert descriptor is not None
    assert model is None
