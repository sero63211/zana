"""Acquisition limits hardening tests."""

from __future__ import annotations

import pytest

from zana_core.acquisition.limits import (
    MAX_EVENT_COUNT,
    MAX_LINE_BYTES,
    MAX_RETAINED_EVENTS,
    MAX_TOTAL_EVENT_BYTES,
    AcquisitionLimits,
)


class TestAcquisitionLimits:
    def test_absurd_line_limit_rejected(self) -> None:
        with pytest.raises(ValueError):
            AcquisitionLimits(max_line_bytes=MAX_LINE_BYTES + 1)

    def test_absurd_retention_and_event_limits_rejected(self) -> None:
        with pytest.raises(ValueError):
            AcquisitionLimits(max_retained_events=MAX_RETAINED_EVENTS + 1)
        with pytest.raises(ValueError):
            AcquisitionLimits(max_event_count=MAX_EVENT_COUNT + 1)

    def test_consistency_validation(self) -> None:
        with pytest.raises(ValueError):
            AcquisitionLimits(
                max_retained_events=10,
                max_event_count=5,
            )
        with pytest.raises(ValueError):
            AcquisitionLimits(
                max_line_bytes=1024,
                max_total_event_bytes=512,
            )

    def test_hard_upper_bounds_are_conservative(self) -> None:
        assert MAX_LINE_BYTES <= 1 << 20
        assert MAX_TOTAL_EVENT_BYTES <= 1 << 20
        assert MAX_RETAINED_EVENTS <= MAX_EVENT_COUNT

    def test_default_concurrency_is_real(self) -> None:
        limits = AcquisitionLimits()
        assert limits.max_concurrent_acquisitions == 1

    def test_false_deadline_knob_removed(self) -> None:
        limits = AcquisitionLimits()
        assert not hasattr(limits, "default_deadline_seconds")
