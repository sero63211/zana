"""Shared bounded runtime discovery and persistence service."""

from __future__ import annotations

import hashlib
import ipaddress
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy.orm import Session, sessionmaker

from zana_core.acquisition.redact import sanitize_terminal_error
from zana_core.db.models import Job, Model, Runtime
from zana_core.db.unit_of_work import UnitOfWork
from zana_core.domain.enums import (
    JobKind,
    JobStatus,
    RuntimeKind,
    RuntimeSource,
    RuntimeStatus,
)
from zana_core.jobs.services import JobNotFoundError, JobService
from zana_core.runtimes.base import (
    AdapterType,
    ProbeTarget,
    RuntimeDescriptor,
)
from zana_core.runtimes.registry import RuntimeProbeRegistry


@dataclass(frozen=True)
class RuntimeSnapshot:
    """Detached runtime identity captured before network work starts."""

    id: int
    kind: RuntimeKind
    endpoint: str
    source: RuntimeSource
    status: RuntimeStatus

    @property
    def identity(self) -> str:
        return runtime_identity(self.kind, self.endpoint, self.source)


def runtime_identity(kind: RuntimeKind, endpoint: str, source: RuntimeSource) -> str:
    """Return a bounded digest of the exact runtime identity, never the URL."""
    raw = f"{kind.value}|{endpoint}|{source.value}".encode()
    return hashlib.sha256(raw).hexdigest()


def _adapter_for_kind(kind: RuntimeKind) -> AdapterType:
    return {
        RuntimeKind.OLLAMA: AdapterType.OLLAMA,
        RuntimeKind.LM_STUDIO: AdapterType.LM_STUDIO,
        RuntimeKind.LLAMA_CPP: AdapterType.LLAMA_CPP,
        RuntimeKind.MLX_LM: AdapterType.MLX_LM,
        RuntimeKind.OPENAI_COMPATIBLE: AdapterType.OPENAI_COMPATIBLE,
        RuntimeKind.UNKNOWN: AdapterType.OPENAI_COMPATIBLE,
    }[kind]


def _is_loopback_endpoint(endpoint: str) -> bool:
    """Return whether an endpoint is a safe loopback-only probe target."""
    try:
        parts = urlsplit(endpoint)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    host = (parts.hostname or "").rstrip(".")
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    ip_host = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        return bool(ipaddress.ip_address(ip_host).is_loopback)
    except ValueError:
        return False


def _descriptor_metadata(descriptor: RuntimeDescriptor) -> dict[str, Any]:
    return {
        "identified_vendor": descriptor.identified_vendor,
        "registered": descriptor.registered,
        "server_running": descriptor.server_running,
        "installed": descriptor.installed,
        "installed_not_running": descriptor.installed_not_running,
        "evidence": descriptor.evidence,
        "warnings": descriptor.warnings,
        "error": descriptor.error,
    }


def _model_metadata(model: Any) -> dict[str, Any]:
    return {
        "display_name": model.display_name,
        "parameter_label": model.parameter_label,
        "trainability": model.trainability,
        "metadata_source": model.metadata_source,
        "runtime_id": model.runtime_id,
    }


class RuntimeDiscoveryService:
    """One bounded probe/sync path shared by manual and post-pull refresh."""

    def __init__(self, registry: RuntimeProbeRegistry) -> None:
        self.registry = registry

    def targets(self, uow: UnitOfWork) -> list[ProbeTarget]:
        """Combine standard loopback candidates with manual loopback runtimes."""
        targets: list[ProbeTarget] = list(self.registry.default_targets())
        for manual in uow.runtimes.list_manual():
            if not _is_loopback_endpoint(manual.endpoint):
                continue
            targets.append(
                ProbeTarget(
                    runtime_id=f"manual:{manual.id}",
                    kind=manual.kind,
                    endpoint=manual.endpoint,
                    source=RuntimeSource.MANUAL,
                    adapter_type=_adapter_for_kind(manual.kind),
                )
            )
        return targets

    def refresh(self, session_factory: sessionmaker[Session]) -> Job | None:
        """Run one bounded discovery refresh with no UoW held across probing.

        A short UoW creates the RUNNING job and snapshots bounded targets,
        then closes. The registry probe runs with no open DB transaction, and
        a fresh short UoW atomically syncs descriptors and marks success (or
        records a sanitized failure after the savepoint rolls back).
        """
        with UnitOfWork(session_factory) as uow:
            service = JobService(uow)
            job = service.create_job(
                JobKind.RUNTIME_REFRESH,
                phase="discovery",
                message="Refreshing runtime and model discovery.",
            )
            service.transition_job(job.id, JobStatus.RUNNING, phase="discovery")
            targets = self.targets(uow)
            job_id = job.id
        try:
            descriptors = self.registry.probe(targets)
        except Exception:  # noqa: BLE001 - failures are sanitized below
            return self._refresh_failed(session_factory, job_id)
        if type(descriptors) is not list:
            return self._refresh_failed(session_factory, job_id)
        sync_failed = False
        with UnitOfWork(session_factory) as uow:
            service = JobService(uow)
            job = service.get_job(job_id)
            if job is None:
                return None
            try:
                with uow.session.begin_nested():
                    self.sync(uow, descriptors)
            except Exception:  # noqa: BLE001 - savepoint rolls back partial sync
                sync_failed = True
            else:
                job = service.transition_job(
                    job.id,
                    JobStatus.SUCCEEDED,
                    phase="complete",
                    message=(
                        f"Runtime discovery complete; {len(descriptors)} candidate(s) probed."
                    ),
                )
        if sync_failed:
            return self._refresh_failed(session_factory, job_id)
        return job

    @staticmethod
    def _refresh_failed(
        session_factory: sessionmaker[Session],
        job_id: int,
    ) -> Job | None:
        with UnitOfWork(session_factory) as uow:
            service = JobService(uow)
            try:
                job = service.get_job(job_id)
            except JobNotFoundError:
                return None
            return service.transition_job(
                job.id,
                JobStatus.FAILED,
                phase="failed",
                message="Runtime discovery could not complete.",
                error=sanitize_terminal_error(
                    code="RUNTIME_REFRESH_FAILED",
                    message="Runtime discovery could not complete.",
                    actions=["retry_refresh"],
                ),
            )

    def sync(self, uow: UnitOfWork, descriptors: list[RuntimeDescriptor]) -> int:
        """Upsert discovered runtimes and their bounded model descriptors.

        An online, registered runtime's returned model set is authoritative for
        that runtime, so models that disappeared from discovery are removed.
        Offline/failed/unregistered descriptors never prune persisted models.
        """
        model_count = 0
        for descriptor in descriptors:
            if type(descriptor) is not RuntimeDescriptor:
                continue
            runtime = uow.runtimes.get_by_kind_endpoint(
                descriptor.kind,
                descriptor.endpoint,
                descriptor.source,
            )
            if runtime is None:
                runtime = Runtime(
                    kind=descriptor.kind,
                    endpoint=descriptor.endpoint,
                    source=descriptor.source,
                    status=descriptor.status,
                    metadata_json=_descriptor_metadata(descriptor),
                    last_seen_at=descriptor.last_seen_at,
                )
                uow.runtimes.add(runtime)
            else:
                runtime.kind = descriptor.kind
                runtime.status = descriptor.status
                runtime.metadata_json = _descriptor_metadata(descriptor)
                runtime.last_seen_at = descriptor.last_seen_at
            uow.session.flush()
            for model in descriptor.models:
                key = f"{runtime.id}:{model.model_id}"
                existing = uow.models.get(key)
                if existing is None:
                    uow.models.add(
                        Model(
                            key=key,
                            runtime_id=runtime.id,
                            model_id=model.model_id,
                            digest=model.digest,
                            family=model.family,
                            format=model.format,
                            quantization=model.quantization,
                            parameter_count=model.parameter_count,
                            size_bytes=model.size_bytes,
                            context_length=model.context_length,
                            capabilities_json=model.capabilities,
                            identity_strength=model.identity_strength,
                            metadata_json=_model_metadata(model),
                            last_seen_at=model.last_seen_at,
                        )
                    )
                else:
                    existing.model_id = model.model_id
                    existing.digest = model.digest
                    existing.family = model.family
                    existing.format = model.format
                    existing.quantization = model.quantization
                    existing.parameter_count = model.parameter_count
                    existing.size_bytes = model.size_bytes
                    existing.context_length = model.context_length
                    existing.capabilities_json = model.capabilities
                    existing.identity_strength = model.identity_strength
                    existing.metadata_json = _model_metadata(model)
                    existing.last_seen_at = model.last_seen_at
                model_count += 1
            if descriptor.status == RuntimeStatus.ONLINE and descriptor.registered:
                retained_keys = {f"{runtime.id}:{model.model_id}" for model in descriptor.models}
                for existing_model in uow.models.list_by_runtime(runtime.id):
                    if existing_model.key not in retained_keys:
                        uow.models.delete(existing_model)
        return model_count

    def probe_runtime(self, snapshot: RuntimeSnapshot) -> RuntimeDescriptor | None:
        """Probe exactly one runtime identity, never a broader scan."""
        if not _is_loopback_endpoint(snapshot.endpoint):
            return None
        adapter_type = _adapter_for_kind(snapshot.kind)
        runtime_id = (
            f"manual:{snapshot.id}"
            if snapshot.source is RuntimeSource.MANUAL
            else f"auto:{snapshot.id}"
        )
        target = ProbeTarget(
            runtime_id=runtime_id,
            kind=snapshot.kind,
            endpoint=snapshot.endpoint,
            source=snapshot.source,
            adapter_type=adapter_type,
        )
        descriptors = self.registry.probe([target])
        for descriptor in descriptors:
            if (
                type(descriptor) is RuntimeDescriptor
                and descriptor.kind == snapshot.kind
                and descriptor.endpoint == snapshot.endpoint
                and descriptor.source == snapshot.source
            ):
                return descriptor
        return None

    def confirm_model(
        self,
        session_factory: sessionmaker[Session],
        snapshot: RuntimeSnapshot,
        model_reference: str,
    ) -> tuple[RuntimeDescriptor | None, Model | None]:
        """Refresh exactly one runtime and confirm the acquired model digest.

        The runtime is revalidated in short transactions around the probe;
        no database session is held while the registry performs network work.
        """
        with UnitOfWork(session_factory) as uow:
            runtime = uow.runtimes.get(snapshot.id)
            if runtime is None:
                return None, None
            if not self._snapshot_matches(runtime, snapshot):
                return None, None
            if runtime.status is not RuntimeStatus.ONLINE:
                return None, None
        descriptor = self.probe_runtime(snapshot)
        if descriptor is None:
            return None, None
        with UnitOfWork(session_factory) as uow:
            runtime = uow.runtimes.get(snapshot.id)
            if runtime is None:
                return None, None
            if not self._snapshot_matches(runtime, snapshot):
                return None, None
            if runtime.status is not RuntimeStatus.ONLINE:
                return None, None
            self.sync(uow, [descriptor])
            model = self.find_model(uow, runtime.id, model_reference)
        return descriptor, model

    @staticmethod
    def _snapshot_matches(
        runtime: Runtime | None,
        snapshot: RuntimeSnapshot,
    ) -> bool:
        if runtime is None:
            return False
        return (
            runtime.kind == snapshot.kind
            and runtime.endpoint == snapshot.endpoint
            and runtime.source == snapshot.source
            and runtime_identity(runtime.kind, runtime.endpoint, runtime.source)
            == snapshot.identity
        )

    @staticmethod
    def find_model(
        uow: UnitOfWork,
        runtime_id: int,
        model_reference: str,
    ) -> Model | None:
        for model in uow.models.list_by_runtime(runtime_id):
            if model.model_id == model_reference and model.digest:
                return model
        return None
