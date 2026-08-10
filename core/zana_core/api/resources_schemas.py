"""Local API schemas for the bounded resource surface."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from zana_core.resources.models import OperationCategory, PlatformLabel


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class ResourceSnapshotRead(_Strict):
    revision: int
    captured_at: datetime
    age_seconds: float
    fresh: bool
    platform: PlatformLabel
    os_name: str
    arch: str
    logical_cores: int | None
    memory_total_bytes: int | None
    memory_available_bytes: int | None
    disk_path: str
    disk_free_bytes: int | None
    probe_error_code: str | None
    probe_status: str


class CategoryLimitRead(_Strict):
    category: OperationCategory
    max_concurrency: int
    max_workers: int
    max_memory_bytes: int | None
    max_disk_bytes: int | None
    max_items: int | None
    max_bytes: int | None
    max_open_files: int | None
    max_recursion_depth: int | None
    tiny: bool
    allow_unknown_size: bool


class ResourcePolicyRead(_Strict):
    revision: int
    memory_reserve_bytes: int
    disk_reserve_bytes: int
    safety_reserve_fraction: float
    disk_overhead_fraction: float
    max_open_files: int | None
    max_recursion_depth: int | None
    auto_heavy_concurrency: bool
    max_heavy_concurrency: int
    large_host_min_memory_bytes: int
    large_host_min_cores: int
    categories: list[CategoryLimitRead]


class ResourceLeaseRead(_Strict):
    token: str
    request_id: str
    category: OperationCategory
    policy_revision: int
    snapshot_revision: int
    memory_bytes: int
    disk_bytes: int
    workers: int
    items: int
    bytes_accounted: int
    open_files: int
    active: bool


class ResourceUsageRead(_Strict):
    token: str
    request_id: str
    category: OperationCategory
    policy_revision: int
    snapshot_revision: int
    memory_bytes: int
    disk_bytes: int
    workers: int
    items: int
    bytes_accounted: int
    open_files: int
    released: bool
    sequence: int


class ResourceUsagePageRead(_Strict):
    items: list[ResourceUsageRead]
    count: int
    limit: int
    next_cursor: int | None
    truncated: bool
    total_available: int
