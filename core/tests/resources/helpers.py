"""Shared snapshot/request builders for resource governor tests."""

from __future__ import annotations

from zana_core.resources.governor import ResourceGovernor
from zana_core.resources.models import (
    OperationCategory,
    OperationRequest,
    PlatformLabel,
    ResourcePolicy,
    ResourceSnapshot,
)
from zana_core.resources.snapshot import SnapshotProvider

GIB = 1 << 30


def snapshot(
    *,
    revision: int = 0,
    platform: PlatformLabel = PlatformLabel.MACOS,
    memory_total: int | None = 16 * GIB,
    memory_available: int | None = 12 * GIB,
    disk_free: int | None = 100 * GIB,
    cores: int | None = 12,
    probe_error: str | None = None,
) -> ResourceSnapshot:
    return ResourceSnapshot(
        revision=revision,
        platform=platform,
        os_name="test",
        arch="arm64",
        logical_cores=cores,
        memory_total_bytes=memory_total,
        memory_available_bytes=memory_available,
        disk_path="/tmp",
        disk_free_bytes=disk_free,
        probe_error=probe_error,
        notes=(probe_error,) if probe_error else (),
    )


def request(
    *,
    category: OperationCategory = OperationCategory.TRAINING,
    request_id: str = "req-1",
    memory: int | None = None,
    disk: int | None = None,
    workers: int | None = None,
    items: int | None = None,
    byte_count: int | None = None,
    open_files: int | None = None,
    recursion: int | None = None,
    ttl: int | None = None,
) -> OperationRequest:
    category = OperationCategory(category)
    return OperationRequest(
        id=request_id,
        category=category,
        name=category.value,
        required_memory_bytes=memory,
        required_disk_bytes=disk,
        requested_workers=workers,
        items_count=items,
        byte_count=byte_count,
        open_files=open_files,
        recursion_depth=recursion,
        ttl_seconds=ttl,
    )


class FixedSnapshotProvider:
    """Deterministic injected snapshot provider for tests."""

    def __init__(self, snap: ResourceSnapshot) -> None:
        self._snap = snap
        self.calls = 0

    def capture(self) -> ResourceSnapshot:
        self.calls += 1
        return self._snap


class FailingSnapshotProvider:
    """Provider that always fails; governor must surface unknown fields."""

    def capture(self) -> ResourceSnapshot:
        raise RuntimeError("probe failed")


def make_governor(
    *,
    policy: ResourcePolicy | None = None,
    snap: ResourceSnapshot | None = None,
    provider: SnapshotProvider | None = None,
) -> ResourceGovernor:
    if policy is None:
        policy = ResourcePolicy()
    if provider is None:
        provider = FixedSnapshotProvider(snap or snapshot())
    return ResourceGovernor(policy, provider)
