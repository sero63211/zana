"""Snapshot provider behavior: platform mapping and probe failure honesty."""

from __future__ import annotations

import pytest

from tests.resources.helpers import FailingSnapshotProvider, make_governor
from zana_core.resources.models import AdmissionOutcome, DenialReason, PlatformLabel
from zana_core.resources.snapshot import DefaultSnapshotProvider, platform_label


def test_platform_label_mapping():
    assert platform_label("Darwin") == PlatformLabel.MACOS
    assert platform_label("Linux") == PlatformLabel.LINUX
    assert platform_label("Windows") == PlatformLabel.WINDOWS
    assert platform_label("Haiku") == PlatformLabel.UNKNOWN
    assert platform_label(None) == PlatformLabel.UNKNOWN


def test_default_provider_returns_real_or_unknown_fields():
    provider = DefaultSnapshotProvider(".")
    snap = provider.capture()
    assert snap.revision == 0
    if snap.memory_total_bytes is not None:
        assert snap.memory_total_bytes > 0
    if snap.memory_available_bytes is not None:
        assert snap.memory_available_bytes >= 0
    if snap.disk_free_bytes is not None:
        assert snap.disk_free_bytes > 0
    assert snap.probe_error is None or snap.probe_error


def test_provider_failure_surfaces_unknown_not_fake_zero():
    governor = make_governor(provider=FailingSnapshotProvider())
    snap = governor.snapshot
    assert snap.platform == PlatformLabel.UNKNOWN
    assert snap.memory_total_bytes is None
    assert snap.memory_available_bytes is None
    assert snap.disk_free_bytes is None
    assert "probe failed" in (snap.probe_error or "")


def test_heavy_admit_with_unknown_snapshot_asks_never_allows():
    from tests.resources.helpers import request

    governor = make_governor(provider=FailingSnapshotProvider())
    decision = governor.admit(request(category="training", memory=1 << 30, disk=1 << 30))
    assert decision.outcome == AdmissionOutcome.ASK
    assert decision.reason == DenialReason.UNKNOWN_HEADROOM


def test_tiny_admit_still_works_with_unknown_snapshot():
    from tests.resources.helpers import request

    governor = make_governor(provider=FailingSnapshotProvider())
    decision = governor.admit(request(category="tiny"))
    assert decision.outcome == AdmissionOutcome.ALLOW
    governor.release(decision.lease.token)


@pytest.mark.parametrize(
    "system",
    ["Darwin", "Linux", "Windows"],
)
def test_real_provider_platform_label_matches(system, monkeypatch):
    monkeypatch.setattr("platform.system", lambda: system)
    provider = DefaultSnapshotProvider(".")
    assert (
        provider.capture().platform.value
        == {
            "Darwin": "macos",
            "Linux": "linux",
            "Windows": "windows",
        }[system]
    )
