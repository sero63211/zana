"""Focused tests for the resource service read/lease/usage surface."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from zana_core.resources.governor import ResourceGovernor
from zana_core.resources.models import (
    OperationCategory,
    OperationRequest,
    PlatformLabel,
    ResourcePolicy,
    ResourceSnapshot,
)
from zana_core.resources.service import (
    MAX_USAGE_PAGE_LIMIT,
    RESOURCE_POLICY_REVISION,
    ResourceService,
)


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


def _request(request_id: str = "req-1") -> OperationRequest:
    return OperationRequest(
        id=request_id,
        category=OperationCategory.TINY,
        name="tiny-operation",
        required_memory_bytes=1 << 20,
        required_disk_bytes=2 << 20,
    )


def test_snapshot_fresh_then_stale() -> None:
    clock = Clock()
    service = ResourceService(
        provider=FixedSnapshotProvider(_snapshot()),
        now=clock,
        stale_after_seconds=30,
    )
    first = service.snapshot()
    assert first.fresh is True
    assert first.probe_status == "ok"
    assert first.probe_error_code is None
    clock.advance(31)
    stale = service.snapshot()
    assert stale.fresh is False
    assert stale.age_seconds >= 31


def test_refresh_updates_revision_and_captured_at() -> None:
    provider = FixedSnapshotProvider(_snapshot())
    service = ResourceService(provider=provider, now=Clock())
    before = service.snapshot()
    assert before.snapshot.revision == 0
    refreshed = service.refresh()
    assert refreshed.snapshot.revision == 1
    assert provider.calls >= 2
    assert refreshed.fresh is True


def test_unknown_probe_state_is_explicit() -> None:
    service = ResourceService(provider=FailingSnapshotProvider(), now=Clock())
    view = service.snapshot()
    assert view.probe_status == "unavailable"
    assert view.probe_error_code == "SNAPSHOT_PROVIDER_UNAVAILABLE"
    assert view.snapshot.memory_total_bytes is None
    assert view.snapshot.memory_available_bytes is None
    assert view.snapshot.disk_free_bytes is None


def test_partial_probe_state_is_explicit() -> None:
    snap = _snapshot(memory_available_bytes=None, disk_free_bytes=5 << 30)
    service = ResourceService(provider=FixedSnapshotProvider(snap), now=Clock())
    view = service.snapshot()
    assert view.probe_status == "partial"
    assert view.snapshot.memory_available_bytes is None
    assert view.snapshot.disk_free_bytes == 5 << 30


def test_policy_revision_is_stable_and_typed() -> None:
    service = ResourceService(provider=FixedSnapshotProvider(_snapshot()), now=Clock())
    policy = service.policy()
    assert policy.memory_reserve_bytes > 0
    assert service.governor.policy is policy
    assert RESOURCE_POLICY_REVISION == 1


def test_active_leases_reflect_admitted_operations() -> None:
    governor = ResourceGovernor(ResourcePolicy(), FixedSnapshotProvider(_snapshot()))
    service = ResourceService(governor=governor, now=Clock())
    first = service.admit(_request("a"))
    second = service.admit(_request("b"))
    assert first.lease is not None
    assert second.lease is not None
    assert len(service.active_leases()) == 2
    service.release(first.lease.token)
    assert [lease.request_id for lease in service.active_leases()] == ["b"]


def test_usage_page_descending_and_cursor() -> None:
    governor = ResourceGovernor(ResourcePolicy(), FixedSnapshotProvider(_snapshot()))
    service = ResourceService(governor=governor, now=Clock())
    tokens: list[str] = []
    for index in range(1, 6):
        decision = service.admit(_request(f"req-{index}"))
        assert decision.lease is not None
        tokens.append(decision.lease.token)
    for token in tokens:
        service.release(token)
    page1 = service.usage_page(limit=2)
    assert page1.count == 2
    assert page1.truncated is True
    assert page1.next_cursor == 9
    assert [record.sequence for record in page1.items] == [10, 9]
    page2 = service.usage_page(limit=2, before_sequence=page1.next_cursor)
    assert [record.sequence for record in page2.items] == [8, 7]
    assert page2.next_cursor == 7


def test_usage_page_rejects_bad_bounds() -> None:
    service = ResourceService(provider=FixedSnapshotProvider(_snapshot()), now=Clock())
    with pytest.raises(ValueError):
        service.usage_page(limit=0)
    with pytest.raises(ValueError):
        service.usage_page(limit=MAX_USAGE_PAGE_LIMIT + 1)
    with pytest.raises(ValueError):
        service.usage_page(before_sequence=-1)
    with pytest.raises(ValueError):
        service.usage_page(before_sequence=True)  # type: ignore[arg-type]


def test_service_rejects_bad_constructor_types() -> None:
    with pytest.raises(TypeError):
        ResourceService(governor=object())  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ResourceService(provider=FixedSnapshotProvider(_snapshot()), stale_after_seconds=-1)
