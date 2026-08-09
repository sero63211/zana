"""Authenticated runtime registry endpoints."""

from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request, Response
from starlette.status import HTTP_204_NO_CONTENT

from zana_core.api.deps import UnitOfWorkDep, verify_token
from zana_core.api.errors import http_error
from zana_core.api.schemas import JobRead, RuntimeCreate, RuntimeRead
from zana_core.db.models import Job, Model, Runtime
from zana_core.db.unit_of_work import UnitOfWork
from zana_core.domain.enums import (
    JobKind,
    JobStatus,
    RuntimeKind,
    RuntimeSource,
    RuntimeStatus,
)
from zana_core.jobs.services import JobService
from zana_core.runtimes.base import AdapterType, ProbeTarget, RuntimeDescriptor
from zana_core.runtimes.registry import RuntimeProbeRegistry

router = APIRouter(
    prefix="/api/v1/runtimes",
    tags=["runtimes"],
    dependencies=[Depends(verify_token)],
)


def _validate_manual_endpoint(endpoint: str) -> None:
    parsed = urlsplit(endpoint)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise http_error(
            422,
            "INVALID_ENDPOINT",
            "A manual runtime endpoint must be an absolute http(s) URL.",
            recoverable=True,
            actions=["fix_endpoint"],
        )
    if parsed.username is not None or parsed.password is not None:
        raise http_error(
            422,
            "ENDPOINT_CREDENTIALS_NOT_ALLOWED",
            "Do not embed credentials in runtime endpoints.",
            recoverable=True,
            actions=["store_credentials_separately"],
        )


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


def _probe_targets(
    uow: UnitOfWork,
    registry: RuntimeProbeRegistry,
) -> list[ProbeTarget]:
    """Combine standard loopback candidates with manual loopback runtimes."""
    targets: list[ProbeTarget] = list(registry.default_targets())
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


def _sync_discovery(uow: UnitOfWork, descriptors: list[RuntimeDescriptor]) -> int:
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


@router.post("/refresh", response_model=JobRead)
def refresh_runtimes(request: Request, uow: UnitOfWorkDep) -> Job:
    """Run bounded runtime discovery and persist the real results as a job.

    Discovery persistence runs inside a savepoint so a failed probe cannot
    leave partial runtime/model rows, while the FAILED job and its event still
    commit and remain fetchable from the jobs API.
    """
    registry: RuntimeProbeRegistry = request.app.state.runtime_registry
    service = JobService(uow)
    job = service.create_job(
        JobKind.RUNTIME_REFRESH,
        phase="discovery",
        message="Refreshing runtime and model discovery.",
    )
    service.transition_job(job.id, JobStatus.RUNNING, phase="discovery")
    try:
        with uow.session.begin_nested():
            descriptors = registry.probe(_probe_targets(uow, registry))
            _sync_discovery(uow, descriptors)
        return service.transition_job(
            job.id,
            JobStatus.SUCCEEDED,
            phase="complete",
            message=f"Runtime discovery complete; {len(descriptors)} candidate(s) probed.",
        )
    except Exception:  # noqa: BLE001 - failures are sanitized below
        return service.transition_job(
            job.id,
            JobStatus.FAILED,
            phase="failed",
            message="Runtime discovery could not complete.",
            error={
                "code": "RUNTIME_REFRESH_FAILED",
                "message": "Runtime discovery could not complete.",
                "recoverable": True,
                "actions": ["retry_refresh"],
            },
        )


@router.get("", response_model=list[RuntimeRead])
def list_runtimes(uow: UnitOfWorkDep) -> list[Runtime]:
    return uow.runtimes.list()


@router.post("/manual", response_model=RuntimeRead, status_code=201)
def create_manual_runtime(payload: RuntimeCreate, uow: UnitOfWorkDep) -> Runtime:
    _validate_manual_endpoint(payload.endpoint)
    existing = uow.runtimes.get_by_endpoint(payload.endpoint, RuntimeSource.MANUAL)
    if existing is not None:
        raise http_error(
            409,
            "RUNTIME_ALREADY_EXISTS",
            "A manual runtime with this endpoint already exists.",
            recoverable=True,
            actions=["list_runtimes"],
        )
    runtime = Runtime(
        kind=payload.kind,
        endpoint=payload.endpoint,
        source=RuntimeSource.MANUAL,
        status=RuntimeStatus.UNKNOWN,
        metadata_json=payload.metadata_json,
    )
    created = uow.runtimes.add(runtime)
    uow.session.flush()
    return created


@router.delete("/{runtime_id}", status_code=HTTP_204_NO_CONTENT)
def delete_runtime(runtime_id: int, uow: UnitOfWorkDep) -> Response:
    runtime = uow.runtimes.get(runtime_id)
    if runtime is None:
        raise http_error(
            404,
            "RUNTIME_NOT_FOUND",
            "No runtime exists with this id.",
            actions=["list_runtimes"],
        )
    if runtime.source != RuntimeSource.MANUAL:
        raise http_error(
            409,
            "CANNOT_DELETE_AUTO_RUNTIME",
            "Only manually configured runtimes can be removed.",
            recoverable=True,
            actions=["disable_runtime_instead"],
        )
    uow.runtimes.delete(runtime)
    return Response(status_code=HTTP_204_NO_CONTENT)
