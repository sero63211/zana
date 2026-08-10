"""Authenticated bounded resource snapshot, policy, lease, and usage router."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from zana_core.api.deps import verify_token
from zana_core.api.errors import http_error
from zana_core.api.resources_schemas import (
    CategoryLimitRead,
    ResourceLeaseRead,
    ResourcePolicyRead,
    ResourceSnapshotRead,
    ResourceUsagePageRead,
    ResourceUsageRead,
)
from zana_core.resources.models import CategoryLimit, ResourceLease, UsageRecord
from zana_core.resources.service import (
    MAX_USAGE_PAGE_LIMIT,
    RESOURCE_POLICY_REVISION,
    ResourceService,
    SnapshotView,
    UsagePageView,
)
from zana_core.streaming.redaction import redact_value

router = APIRouter(
    prefix="/api/v1/resources",
    tags=["resources"],
    dependencies=[Depends(verify_token)],
)


def _service(request: Request) -> ResourceService:
    service = getattr(request.app.state, "resource_service", None)
    if type(service) is not ResourceService:
        raise http_error(
            503,
            "RESOURCES_SERVICE_UNAVAILABLE",
            "The resource service is not configured on this Core app.",
            recoverable=True,
            actions=["register_resource_routes"],
        )
    return service


@router.get("/snapshot", response_model=ResourceSnapshotRead)
def resource_snapshot(request: Request) -> ResourceSnapshotRead:
    """Return the last explicitly captured real snapshot with freshness state."""
    return _snapshot_read(_service(request).snapshot())


@router.post("/snapshot/refresh", response_model=ResourceSnapshotRead)
def refresh_resource_snapshot(request: Request) -> ResourceSnapshotRead:
    """Explicitly capture a new real snapshot revision; no sampler ever runs."""
    return _snapshot_read(_service(request).refresh())


@router.get("/policy", response_model=ResourcePolicyRead)
def resource_policy(request: Request) -> ResourcePolicyRead:
    policy = _service(request).policy()
    return ResourcePolicyRead(
        revision=RESOURCE_POLICY_REVISION,
        memory_reserve_bytes=policy.memory_reserve_bytes,
        disk_reserve_bytes=policy.disk_reserve_bytes,
        safety_reserve_fraction=policy.safety_reserve_fraction,
        disk_overhead_fraction=policy.disk_overhead_fraction,
        max_open_files=policy.max_open_files,
        max_recursion_depth=policy.max_recursion_depth,
        auto_heavy_concurrency=policy.auto_heavy_concurrency,
        max_heavy_concurrency=policy.max_heavy_concurrency,
        large_host_min_memory_bytes=policy.large_host_min_memory_bytes,
        large_host_min_cores=policy.large_host_min_cores,
        categories=[_category_read(limit) for limit in policy.categories.values()],
    )


@router.get("/leases", response_model=list[ResourceLeaseRead])
def resource_leases(request: Request) -> list[ResourceLeaseRead]:
    return [_lease_read(lease) for lease in _service(request).active_leases()]


@router.get("/usage", response_model=ResourceUsagePageRead)
def resource_usage(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=MAX_USAGE_PAGE_LIMIT)] = 50,
    before_sequence: Annotated[int | None, Query(ge=0)] = None,
) -> ResourceUsagePageRead:
    """Return recent real usage records, newest first, with a bounded cursor."""
    try:
        page = _service(request).usage_page(limit=limit, before_sequence=before_sequence)
    except ValueError:
        raise http_error(
            400,
            "INVALID_USAGE_CURSOR",
            "Usage pagination bounds are invalid.",
            recoverable=True,
            actions=["fix_page_bounds"],
        ) from None
    return _usage_page_read(page)


def _snapshot_read(view: SnapshotView) -> ResourceSnapshotRead:
    raw = view.snapshot
    redacted_path = "" if not raw.disk_path else redact_value({"path": raw.disk_path}).get("path")
    return ResourceSnapshotRead(
        revision=raw.revision,
        captured_at=view.captured_at,
        age_seconds=round(view.age_seconds, 3),
        fresh=view.fresh,
        platform=raw.platform,
        os_name=raw.os_name,
        arch=raw.arch,
        logical_cores=raw.logical_cores,
        memory_total_bytes=raw.memory_total_bytes,
        memory_available_bytes=raw.memory_available_bytes,
        disk_path=redacted_path if type(redacted_path) is str else "",
        disk_free_bytes=raw.disk_free_bytes,
        probe_error_code=view.probe_error_code,
        probe_status=view.probe_status,
    )


def _category_read(limit: CategoryLimit) -> CategoryLimitRead:
    return CategoryLimitRead(
        category=limit.category,
        max_concurrency=limit.max_concurrency,
        max_workers=limit.max_workers,
        max_memory_bytes=limit.max_memory_bytes,
        max_disk_bytes=limit.max_disk_bytes,
        max_items=limit.max_items,
        max_bytes=limit.max_bytes,
        max_open_files=limit.max_open_files,
        max_recursion_depth=limit.max_recursion_depth,
        tiny=limit.tiny,
        allow_unknown_size=limit.allow_unknown_size,
    )


def _lease_read(lease: ResourceLease) -> ResourceLeaseRead:
    return ResourceLeaseRead(
        token=lease.token,
        request_id=lease.request_id,
        category=lease.category,
        policy_revision=lease.policy_revision,
        snapshot_revision=lease.snapshot_revision,
        memory_bytes=lease.memory_bytes,
        disk_bytes=lease.disk_bytes,
        workers=lease.workers,
        items=lease.items,
        bytes_accounted=lease.bytes_accounted,
        open_files=lease.open_files,
        active=lease.active,
    )


def _usage_read(record: UsageRecord) -> ResourceUsageRead:
    return ResourceUsageRead(
        token=record.token,
        request_id=record.request_id,
        category=record.category,
        policy_revision=record.policy_revision,
        snapshot_revision=record.snapshot_revision,
        memory_bytes=record.memory_bytes,
        disk_bytes=record.disk_bytes,
        workers=record.workers,
        items=record.items,
        bytes_accounted=record.bytes_accounted,
        open_files=record.open_files,
        released=record.released,
        sequence=record.sequence,
    )


def _usage_page_read(page: UsagePageView) -> ResourceUsagePageRead:
    return ResourceUsagePageRead(
        items=[_usage_read(record) for record in page.items],
        count=page.count,
        limit=page.limit,
        next_cursor=page.next_cursor,
        truncated=page.truncated,
        total_available=page.total_available,
    )
