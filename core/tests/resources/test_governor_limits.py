"""Worker, file, recursion, item, byte, and category cap enforcement."""

from __future__ import annotations

from tests.resources.helpers import make_governor, request, snapshot
from zana_core.resources.models import (
    AdmissionOutcome,
    DenialReason,
    OperationCategory,
    ResourcePolicy,
)


def test_worker_limit_enforced():
    governor = make_governor(snap=snapshot())
    decision = governor.admit(request(category="training", workers=4))
    assert decision.outcome == AdmissionOutcome.BLOCK
    assert decision.reason == DenialReason.WORKER_LIMIT
    assert decision.recovery.value == "reduce_workers"


def test_open_file_limit_enforced():
    governor = make_governor(snap=snapshot())
    decision = governor.admit(request(category="build", open_files=10_000))
    assert decision.outcome == AdmissionOutcome.BLOCK
    assert decision.reason == DenialReason.FILE_LIMIT


def test_recursion_limit_enforced():
    governor = make_governor(snap=snapshot())
    decision = governor.admit(request(category="build", recursion=10_000))
    assert decision.outcome == AdmissionOutcome.BLOCK
    assert decision.reason == DenialReason.RECURSION_LIMIT


def test_item_and_byte_limits_enforced():
    policy = ResourcePolicy()
    tiny = policy.category_limit(OperationCategory.TINY)
    governor = make_governor(policy=policy, snap=snapshot())
    over_items = governor.admit(request(category="tiny", items=tiny.max_items + 1))
    assert over_items.outcome == AdmissionOutcome.BLOCK
    assert over_items.reason == DenialReason.ITEM_LIMIT
    over_bytes = governor.admit(request(category="tiny", byte_count=tiny.max_bytes + 1))
    assert over_bytes.outcome == AdmissionOutcome.BLOCK
    assert over_bytes.reason == DenialReason.BYTE_LIMIT


def test_cumulative_items_across_leases_bounded():
    policy = ResourcePolicy()
    limited = policy.category_limit(OperationCategory.READ_ONLY).model_copy(
        update={"max_items": 10, "max_concurrency": 4}
    )
    policy = policy.model_copy(update={"categories": {OperationCategory.READ_ONLY: limited}})
    governor = make_governor(policy=policy, snap=snapshot())
    first = governor.admit(request(category="read_only", request_id="a", items=6))
    second = governor.admit(request(category="read_only", request_id="b", items=6))
    assert first.outcome == AdmissionOutcome.ALLOW
    assert second.outcome == AdmissionOutcome.BLOCK
    assert second.reason == DenialReason.ITEM_LIMIT
    governor.release(first.lease.token)
    third = governor.admit(request(category="read_only", request_id="c", items=6))
    assert third.outcome == AdmissionOutcome.ALLOW


def test_distinct_category_concurrency_caps():
    policy = ResourcePolicy().model_copy(update={"auto_heavy_concurrency": False})
    inference = policy.category_limit(OperationCategory.INFERENCE).model_copy(
        update={"max_concurrency": 2}
    )
    training = policy.category_limit(OperationCategory.TRAINING).model_copy(
        update={"max_concurrency": 1}
    )
    policy = policy.model_copy(
        update={
            "categories": {
                OperationCategory.INFERENCE: inference,
                OperationCategory.TRAINING: training,
            }
        }
    )
    governor = make_governor(policy=policy, snap=snapshot())
    a = governor.admit(request(category="inference", request_id="a", memory=1 << 20, disk=1 << 20))
    b = governor.admit(request(category="inference", request_id="b", memory=1 << 20, disk=1 << 20))
    c = governor.admit(request(category="inference", request_id="c", memory=1 << 20, disk=1 << 20))
    assert a.outcome == AdmissionOutcome.ALLOW
    assert b.outcome == AdmissionOutcome.ALLOW
    assert c.outcome == AdmissionOutcome.BLOCK
    t = governor.admit(request(category="training", request_id="t", memory=1 << 20, disk=1 << 20))
    assert t.outcome == AdmissionOutcome.ALLOW  # distinct category caps
    governor.release(a.lease.token)
    governor.release(b.lease.token)
    governor.release(t.lease.token)


def test_training_cap_one_even_on_large_host():
    from tests.resources.helpers import GIB

    governor = make_governor(
        snap=snapshot(memory_total=32 * GIB, memory_available=26 * GIB, cores=16)
    )
    a = governor.admit(request(category="training", request_id="a", memory=1 << 20, disk=1 << 20))
    b = governor.admit(request(category="training", request_id="b", memory=1 << 20, disk=1 << 20))
    assert a.outcome == AdmissionOutcome.ALLOW
    assert b.outcome == AdmissionOutcome.BLOCK
