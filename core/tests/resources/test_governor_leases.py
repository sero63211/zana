"""Lease lifecycle: binding, release, double release, expiry, context manager."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.resources.helpers import GIB, FixedSnapshotProvider, make_governor, request, snapshot
from zana_core.resources.governor import (
    ResourceAdmissionError,
    ResourceGovernor,
    ResourceLeaseError,
)
from zana_core.resources.models import AdmissionOutcome, ResourcePolicy


def test_lease_binds_request_policy_and_snapshot_revision():
    governor = make_governor(snap=snapshot(revision=0))
    governor.refresh()
    decision = governor.admit(
        request(category="training", request_id="job-1", memory=1 << 30, disk=1 << 20)
    )
    lease = decision.lease
    assert lease is not None
    assert lease.request_id == "job-1"
    assert lease.policy_revision == 1
    assert lease.snapshot_revision == 1
    assert lease.active is True


def test_release_returns_usage_record_and_restores_accounting():
    governor = make_governor(snap=snapshot())
    decision = governor.admit(
        request(category="training", request_id="r", memory=1 << 30, disk=1 << 20)
    )
    record = governor.release(decision.lease.token)
    assert record.released is True
    assert record.request_id == "r"
    assert governor.active_leases() == ()
    assert governor._active_memory == 0


def test_double_release_fails_cleanly():
    governor = make_governor(snap=snapshot())
    decision = governor.admit(request(category="tiny", request_id="r"))
    governor.release(decision.lease.token)
    with pytest.raises(ResourceLeaseError):
        governor.release(decision.lease.token)


def test_stale_unknown_token_fails_cleanly():
    governor = make_governor(snap=snapshot())
    with pytest.raises(ResourceLeaseError):
        governor.release("L-999999")


def test_ttl_expiry_reaped_only_on_explicit_call():
    now = datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC)

    def clock() -> datetime:
        return now

    governor = ResourceGovernor(ResourcePolicy(), FixedSnapshotProvider(snapshot()), now=clock)
    governor.admit(request(category="tiny", request_id="x", ttl=60))
    assert len(governor.active_leases()) == 1
    # No timer: lease remains active before the explicit reap.
    assert len(governor.reap_expired(now=now)) == 0
    later = now + timedelta(seconds=61)
    expired = governor.reap_expired(now=later)
    assert len(expired) == 1
    assert expired[0].released is True
    assert governor.active_leases() == ()


def test_usage_records_track_lifecycle_deterministically():
    governor = make_governor(snap=snapshot())
    first = governor.admit(request(category="tiny", request_id="a"))
    governor.admit(request(category="tiny", request_id="b"))
    governor.release(first.lease.token)
    records = governor.usage_records()
    assert [record.sequence for record in records] == [1, 2, 3]
    assert records[0].released is False
    assert records[2].released is True
    assert records[2].request_id == "a"


def test_context_manager_releases_on_success_and_exception():
    governor = make_governor(snap=snapshot())
    with governor.lease(
        request(category="training", request_id="ok", memory=1 << 20, disk=1 << 20)
    ) as lease:
        assert lease.active is True
        assert len(governor.active_leases()) == 1
    assert governor.active_leases() == ()

    with (
        pytest.raises(RuntimeError),
        governor.lease(
            request(category="training", request_id="boom", memory=1 << 20, disk=1 << 20)
        ) as lease,
    ):
        raise RuntimeError("boom")
    assert governor.active_leases() == ()


def test_context_manager_raises_admission_error_on_denial():
    governor = make_governor(snap=snapshot())
    with pytest.raises(ResourceAdmissionError), governor.lease(request(category="training")):
        raise AssertionError("must not enter")


def test_cancel_restores_accounting_immediately():
    governor = make_governor(snap=snapshot(memory_total=16 * GIB, memory_available=12 * GIB))
    decision = governor.admit(request(category="training", memory=8 * GIB, disk=1 << 20))
    assert decision.outcome == AdmissionOutcome.ALLOW
    assert governor._active_memory == 8 * GIB
    record = governor.cancel(decision.lease.token)
    assert record.released is True
    assert governor._active_memory == 0
    again = governor.admit(request(category="training", memory=8 * GIB, disk=1 << 20))
    assert again.outcome == AdmissionOutcome.ALLOW
