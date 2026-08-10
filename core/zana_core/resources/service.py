"""Thread-safe bounded read service over the resource admission governor."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime

from zana_core.resources.governor import ResourceGovernor
from zana_core.resources.models import (
    AdmissionDecision,
    OperationRequest,
    ResourceLease,
    ResourcePolicy,
    ResourceSnapshot,
    UsageRecord,
    _Frozen,
)
from zana_core.resources.snapshot import SnapshotProvider

DEFAULT_STALE_AFTER_SECONDS = 30.0
MAX_USAGE_PAGE_LIMIT = 200
RESOURCE_POLICY_REVISION = 1

_PROBE_CODES: tuple[tuple[str, str], ...] = (
    ("memory probe failed", "MEMORY_PROBE_UNAVAILABLE"),
    ("disk probe failed", "DISK_PROBE_UNAVAILABLE"),
    ("snapshot provider failed", "SNAPSHOT_PROVIDER_UNAVAILABLE"),
)

Clock = Callable[[], datetime]


class SnapshotView(_Frozen):
    """Freshness-bounded projection of one captured resource snapshot."""

    snapshot: ResourceSnapshot
    captured_at: datetime
    age_seconds: float
    fresh: bool
    probe_status: str
    probe_error_code: str | None


class UsagePageView(_Frozen):
    """One bounded descending page of real usage records."""

    items: tuple[UsageRecord, ...]
    count: int
    limit: int
    next_cursor: int | None
    truncated: bool
    total_available: int


class ResourceService:
    """Owns one governor and exposes bounded read surfaces thread-safely.

    Snapshot capture happens only at explicit calls (construction and
    ``refresh``); no sampler, poller, or background thread exists here.
    """

    def __init__(
        self,
        governor: ResourceGovernor | None = None,
        *,
        policy: ResourcePolicy | None = None,
        provider: SnapshotProvider | None = None,
        now: Clock | None = None,
        stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
    ) -> None:
        if governor is not None and type(governor) is not ResourceGovernor:
            raise TypeError("governor must be an exact ResourceGovernor or None")
        if type(stale_after_seconds) not in (float, int) or stale_after_seconds < 0:
            raise ValueError("stale_after_seconds must be a non-negative number")
        self._governor = governor or ResourceGovernor(policy or ResourcePolicy(), provider)
        self._now = now or (lambda: datetime.now(UTC))
        self._stale_after_seconds = float(stale_after_seconds)
        self._captured_at = self._now()
        self._lock = threading.RLock()

    def snapshot(self) -> SnapshotView:
        """Return the last explicitly captured snapshot with freshness state."""
        with self._lock:
            self._governor.reap_expired()
            return self._view()

    def refresh(self) -> SnapshotView:
        """Capture a new real snapshot revision at an explicit caller request."""
        with self._lock:
            self._governor.refresh()
            self._captured_at = self._now()
            return self._view()

    def policy(self) -> ResourcePolicy:
        with self._lock:
            return self._governor.policy

    @property
    def governor(self) -> ResourceGovernor:
        """Expose the owned governor for explicit admission by future writers."""
        return self._governor

    def active_leases(self) -> tuple[ResourceLease, ...]:
        with self._lock:
            self._governor.reap_expired()
            return self._governor.active_leases()

    def admit(self, request: OperationRequest) -> AdmissionDecision:
        """Admit one bounded operation under the service lock."""
        with self._lock:
            return self._governor.admit(request)

    def release(self, token: str) -> UsageRecord:
        """Release one lease under the service lock."""
        with self._lock:
            return self._governor.release(token)

    def usage_page(
        self,
        *,
        limit: int = 50,
        before_sequence: int | None = None,
    ) -> UsagePageView:
        """Return one bounded descending page of recent usage records.

        ``before_sequence`` is an exclusive cursor: only records with a smaller
        sequence are returned, so clients page toward older records.
        """
        with self._lock:
            if type(limit) is not int or limit < 1 or limit > MAX_USAGE_PAGE_LIMIT:
                raise ValueError("limit must be an exact int within the usage page cap")
            if before_sequence is not None and (
                type(before_sequence) is not int or before_sequence < 0
            ):
                raise ValueError("before_sequence must be a non-negative exact int or None")
            records = self._governor.usage_records()
            if before_sequence is not None:
                records = tuple(record for record in records if record.sequence < before_sequence)
            total = len(records)
            newest = records[-limit:]
            page = tuple(reversed(newest))
            truncated = len(records) > len(page)
            next_cursor = page[-1].sequence if truncated and page else None
            return UsagePageView(
                items=page,
                count=len(page),
                limit=limit,
                next_cursor=next_cursor,
                truncated=truncated,
                total_available=total,
            )

    def _view(self) -> SnapshotView:
        snapshot = self._governor.snapshot
        age = max(0.0, (self._now() - self._captured_at).total_seconds())
        return SnapshotView(
            snapshot=snapshot,
            captured_at=self._captured_at,
            age_seconds=age,
            fresh=age <= self._stale_after_seconds,
            probe_status=_probe_status(snapshot),
            probe_error_code=_probe_error_code(snapshot.probe_error),
        )


def _probe_status(snapshot: ResourceSnapshot) -> str:
    values = (
        snapshot.memory_total_bytes,
        snapshot.memory_available_bytes,
        snapshot.disk_free_bytes,
    )
    if all(value is not None for value in values):
        return "ok"
    if any(value is not None for value in values):
        return "partial"
    return "unavailable"


def _probe_error_code(error: str | None) -> str | None:
    if error is None:
        return None
    for marker, code in _PROBE_CODES:
        if marker in error:
            return code
    return "PROBE_UNAVAILABLE"
