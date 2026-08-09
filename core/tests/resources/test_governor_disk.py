"""Disk admission, overhead, and low-disk behavior."""

from __future__ import annotations

from tests.resources.helpers import GIB, make_governor, request, snapshot
from zana_core.resources.models import AdmissionOutcome, DenialReason


def test_disk_requirement_includes_overhead_and_reserve():
    governor = make_governor(snap=snapshot(disk_free=10 * GIB))
    reserve = governor.policy.disk_reserve_bytes
    overhead = governor.policy.disk_overhead_fraction
    budget = 10 * GIB - reserve
    disk_req = int((budget // 2) / (1.0 + overhead))
    decision = governor.admit(request(category="export", memory=1 << 20, disk=disk_req))
    assert decision.outcome == AdmissionOutcome.ALLOW
    lease = decision.lease
    assert lease.disk_bytes == int(disk_req * (1.0 + overhead))
    governor.release(lease.token)


def test_low_disk_blocks_heavy_operation():
    governor = make_governor(snap=snapshot(disk_free=1 * GIB))
    decision = governor.admit(request(category="export", memory=1 << 20, disk=1 * GIB))
    assert decision.outcome == AdmissionOutcome.BLOCK
    assert decision.reason == DenialReason.DISK_INSUFFICIENT
    assert decision.recovery.value == "free_disk"


def test_unknown_disk_asks_never_allows():
    governor = make_governor(snap=snapshot(disk_free=100 * GIB))
    decision = governor.admit(request(category="export"))
    assert decision.outcome == AdmissionOutcome.ASK
    assert decision.reason == DenialReason.UNKNOWN_SIZE


def test_unknown_disk_free_asks_for_heavy():
    governor = make_governor(snap=snapshot(disk_free=None))
    decision = governor.admit(request(category="export", memory=1 << 20, disk=1 << 20))
    assert decision.outcome == AdmissionOutcome.ASK
    assert decision.reason == DenialReason.UNKNOWN_HEADROOM


def test_active_disk_accounted_globally():
    governor = make_governor(snap=snapshot(disk_free=10 * GIB))
    reserve = governor.policy.disk_reserve_bytes
    overhead = governor.policy.disk_overhead_fraction
    budget = 10 * GIB - reserve
    first_disk_req = int((budget * 2 // 5) / (1.0 + overhead))
    first = governor.admit(
        request(
            category="export",
            request_id="a",
            memory=1 << 20,
            disk=first_disk_req,
        )
    )
    assert first.outcome == AdmissionOutcome.ALLOW
    first_requirement = first.lease.disk_bytes
    second_disk_req = int((budget - first_requirement + 1) / (1.0 + overhead))
    second = governor.admit(
        request(
            category="build",
            request_id="b",
            memory=1 << 20,
            disk=second_disk_req,
        )
    )
    assert second.outcome == AdmissionOutcome.BLOCK
    assert second.reason == DenialReason.DISK_INSUFFICIENT


def test_category_disk_cap_enforced():
    from zana_core.resources.models import OperationCategory, ResourcePolicy

    policy = ResourcePolicy()
    limited = policy.category_limit(OperationCategory.PORTABILITY).model_copy(
        update={"max_disk_bytes": 2 * GIB}
    )
    policy = policy.model_copy(update={"categories": {OperationCategory.PORTABILITY: limited}})
    governor = make_governor(policy=policy, snap=snapshot(disk_free=100 * GIB))
    decision = governor.admit(request(category="portability", memory=1 << 20, disk=3 * GIB))
    assert decision.outcome == AdmissionOutcome.BLOCK
    assert decision.reason == DenialReason.CATEGORY_LIMIT
