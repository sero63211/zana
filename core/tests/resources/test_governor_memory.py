"""Memory admission behavior across simulated host sizes."""

from __future__ import annotations

import pytest

from tests.resources.helpers import GIB, make_governor, request, snapshot
from zana_core.resources.models import (
    AdmissionOutcome,
    DenialReason,
    OperationCategory,
    ResourcePolicy,
)


@pytest.mark.parametrize(
    ("total", "available", "cores"),
    [
        (4 * GIB, 3 * GIB, 4),
        (8 * GIB, 6 * GIB, 6),
        (16 * GIB, 12 * GIB, 12),
        (32 * GIB, 26 * GIB, 16),
    ],
)
def test_memory_budget_reserves_os_and_safety(total, available, cores):
    snap = snapshot(memory_total=total, memory_available=available, cores=cores)
    governor = make_governor(snap=snap)
    reserve = governor.policy.memory_reserve_bytes
    safety = int(total * governor.policy.safety_reserve_fraction)
    budget = available - reserve - safety
    decision = governor.admit(request(category="training", memory=budget, disk=1 << 20))
    assert decision.outcome == AdmissionOutcome.ALLOW
    governor.release(decision.lease.token)

    over = governor.admit(request(category="training", memory=budget + 1, disk=1 << 20))
    assert over.outcome == AdmissionOutcome.BLOCK
    assert over.reason == DenialReason.MEMORY_INSUFFICIENT
    assert over.recovery.value == "wait_for_headroom"


def test_4gb_constrained_host_uses_single_heavy_concurrency():
    governor = make_governor(snap=snapshot(memory_total=4 * GIB, memory_available=3 * GIB, cores=4))
    first = governor.admit(
        request(category="inference", request_id="a", memory=256 << 20, disk=1 << 20)
    )
    assert first.outcome == AdmissionOutcome.ALLOW
    second = governor.admit(
        request(category="inference", request_id="b", memory=256 << 20, disk=1 << 20)
    )
    assert second.outcome == AdmissionOutcome.BLOCK
    assert second.reason == DenialReason.CONCURRENCY_LIMIT
    governor.release(first.lease.token)
    third = governor.admit(
        request(category="inference", request_id="c", memory=256 << 20, disk=1 << 20)
    )
    assert third.outcome == AdmissionOutcome.ALLOW
    governor.release(third.lease.token)


def test_large_host_allows_bounded_heavy_concurrency():
    policy = ResourcePolicy()
    policy = policy.model_copy(
        update={
            "categories": {
                OperationCategory.INFERENCE: policy.category_limit(
                    OperationCategory.INFERENCE
                ).model_copy(update={"max_concurrency": 2})
            }
        }
    )
    governor = make_governor(
        policy=policy,
        snap=snapshot(memory_total=32 * GIB, memory_available=26 * GIB, cores=16),
    )
    first = governor.admit(
        request(category="inference", request_id="a", memory=512 << 20, disk=1 << 20)
    )
    second = governor.admit(
        request(category="inference", request_id="b", memory=512 << 20, disk=1 << 20)
    )
    assert first.outcome == AdmissionOutcome.ALLOW
    assert second.outcome == AdmissionOutcome.ALLOW
    third = governor.admit(
        request(category="inference", request_id="c", memory=512 << 20, disk=1 << 20)
    )
    assert third.outcome == AdmissionOutcome.BLOCK
    governor.release(first.lease.token)
    governor.release(second.lease.token)


def test_unknown_required_memory_asks_never_allows():
    governor = make_governor(snap=snapshot())
    decision = governor.admit(request(category="training"))
    assert decision.outcome == AdmissionOutcome.ASK
    assert decision.reason == DenialReason.UNKNOWN_SIZE
    assert decision.recovery.value == "provide_estimate"


def test_unknown_total_memory_asks_for_heavy():
    governor = make_governor(
        snap=snapshot(memory_total=None, memory_available=None, disk_free=100 * GIB)
    )
    decision = governor.admit(request(category="training", memory=1 << 30, disk=1 << 20))
    assert decision.outcome == AdmissionOutcome.ASK
    assert decision.reason == DenialReason.UNKNOWN_HEADROOM


def test_tiny_operations_are_cheap_but_bounded():
    governor = make_governor(snap=snapshot())
    for index in range(16):
        decision = governor.admit(request(category="tiny", request_id=f"tiny-{index}"))
        assert decision.outcome == AdmissionOutcome.ALLOW
    blocked = governor.admit(request(category="tiny", request_id="tiny-16"))
    assert blocked.outcome == AdmissionOutcome.BLOCK
    assert blocked.reason == DenialReason.CONCURRENCY_LIMIT
    assert len(governor.active_leases()) == 16


def test_active_leases_are_accounted_before_deciding():
    governor = make_governor(snap=snapshot(memory_total=8 * GIB, memory_available=6 * GIB))
    reserve = governor.policy.memory_reserve_bytes
    safety = int(8 * GIB * governor.policy.safety_reserve_fraction)
    budget = 6 * GIB - reserve - safety
    first = governor.admit(
        request(category="training", request_id="a", memory=budget // 2, disk=1 << 20)
    )
    assert first.outcome == AdmissionOutcome.ALLOW
    second = governor.admit(request(category="build", request_id="b", memory=budget, disk=1 << 20))
    assert second.outcome == AdmissionOutcome.BLOCK
    assert second.reason == DenialReason.MEMORY_INSUFFICIENT


def test_category_memory_cap_enforced():
    policy = ResourcePolicy()
    limited = policy.category_limit(OperationCategory.TRAINING).model_copy(
        update={"max_memory_bytes": 2 * GIB}
    )
    policy = policy.model_copy(update={"categories": {OperationCategory.TRAINING: limited}})
    governor = make_governor(policy=policy, snap=snapshot())
    decision = governor.admit(request(category="training", memory=3 * GIB, disk=1 << 20))
    assert decision.outcome == AdmissionOutcome.BLOCK
    assert decision.reason == DenialReason.CATEGORY_LIMIT
