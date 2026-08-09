"""Deterministic decisions and refresh revision behavior."""

from __future__ import annotations

from tests.resources.helpers import FixedSnapshotProvider, make_governor, request, snapshot
from zana_core.resources.governor import ResourceGovernor
from zana_core.resources.models import AdmissionOutcome, ResourcePolicy


def test_identical_requests_produce_identical_decisions():
    governor = make_governor(snap=snapshot())
    first = governor.admit(request(category="tiny", request_id="a"))
    second = governor.admit(request(category="tiny", request_id="b"))
    assert first.outcome == second.outcome == AdmissionOutcome.ALLOW
    assert first.category == second.category
    assert first.snapshot_revision == second.snapshot_revision


def test_refresh_increments_snapshot_revision():
    provider = FixedSnapshotProvider(snapshot(revision=0))
    governor = ResourceGovernor(ResourcePolicy(), provider)
    assert governor.snapshot.revision == 0
    refreshed = governor.refresh()
    assert refreshed.revision == 1
    decision = governor.admit(request(category="tiny", request_id="a"))
    assert decision.snapshot_revision == 1
    assert decision.lease.snapshot_revision == 1


def test_active_lease_survives_snapshot_refresh():
    provider = FixedSnapshotProvider(snapshot(revision=0))
    governor = ResourceGovernor(ResourcePolicy(), provider)
    decision = governor.admit(request(category="tiny", request_id="keep"))
    governor.refresh()
    assert decision.lease.token in {lease.token for lease in governor.active_leases()}
    record = governor.release(decision.lease.token)
    assert record.released is True
