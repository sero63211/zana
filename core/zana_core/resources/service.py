"""Thread-safe bounded read service over the resource admission governor."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from math import isfinite
from typing import Any

from zana_core.resources.governor import (
    DEFAULT_USAGE_HISTORY_BYTES,
    DEFAULT_USAGE_HISTORY_LIMIT,
    MAX_USAGE_HISTORY_BYTES,
    MAX_USAGE_HISTORY_LIMIT,
    ResourceGovernor,
)
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
MAX_USAGE_HISTORY_DEFAULT = DEFAULT_USAGE_HISTORY_LIMIT
MAX_USAGE_HISTORY_HARD_CAP = MAX_USAGE_HISTORY_LIMIT
MAX_USAGE_HISTORY_BYTES_DEFAULT = DEFAULT_USAGE_HISTORY_BYTES
MAX_USAGE_HISTORY_BYTES_HARD_CAP = MAX_USAGE_HISTORY_BYTES
RESOURCE_POLICY_REVISION = 1
_LEASE_REF_SALT = "zana-resource-lease-ref-v1"
_REQUEST_REF_SALT = "zana-resource-request-ref-v1"

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
    history_limit: int
    history_dropped: int
    history_max_bytes: int
    history_serialized_bytes: int
    history_serialized_bytes_dropped: int
    history_default_limit: int
    history_default_max_bytes: int


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
        usage_history_limit: int = MAX_USAGE_HISTORY_DEFAULT,
        usage_history_max_bytes: int = MAX_USAGE_HISTORY_BYTES_DEFAULT,
    ) -> None:
        if governor is not None:
            if type(governor) is not ResourceGovernor:
                raise TypeError("governor must be an exact ResourceGovernor or None")
            if policy is not None or provider is not None:
                raise ValueError(
                    "policy and provider must be omitted when an exact governor is supplied"
                )
        _require_safe_config(
            stale_after_seconds=stale_after_seconds,
            usage_history_limit=usage_history_limit,
            usage_history_max_bytes=usage_history_max_bytes,
            now=now,
        )
        self._governor = governor or ResourceGovernor(policy or ResourcePolicy(), provider)
        self._now = now or (lambda: datetime.now(UTC))
        self._stale_after_seconds = float(stale_after_seconds)
        self._usage_history_limit = usage_history_limit
        self._usage_history_max_bytes = usage_history_max_bytes
        self._governor.configure_usage_history(
            limit=usage_history_limit,
            max_bytes=usage_history_max_bytes,
        )
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
            (
                retained_count,
                retained_bytes,
                history_dropped,
                history_bytes_dropped,
            ) = self._governor.usage_history_stats()
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
                history_limit=self._usage_history_limit,
                history_dropped=history_dropped,
                history_max_bytes=self._usage_history_max_bytes,
                history_serialized_bytes=retained_bytes,
                history_serialized_bytes_dropped=history_bytes_dropped,
                history_default_limit=MAX_USAGE_HISTORY_DEFAULT,
                history_default_max_bytes=MAX_USAGE_HISTORY_BYTES_DEFAULT,
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


def public_lease_ref(token: str) -> str:
    """One-way stable public reference for a lease token, never the token."""
    digest = sha256((_LEASE_REF_SALT + token).encode("utf-8", errors="replace")).hexdigest()
    return f"lease-{digest[:16]}"


def public_request_ref(request_id: str) -> str:
    """Stable nonsecret public reference for an arbitrary request id."""
    if type(request_id) is not str or not request_id:
        return ""
    digest = sha256((_REQUEST_REF_SALT + request_id).encode("utf-8", errors="replace")).hexdigest()
    return f"request-{digest[:16]}"


def _stale_after_valid(value: float | int) -> bool:
    return type(value) in (float, int) and isfinite(value) and value >= 0


def _usage_history_valid(value: object) -> bool:
    return type(value) is int and 1 <= value <= MAX_USAGE_HISTORY_HARD_CAP


def _usage_bytes_valid(value: object) -> bool:
    return type(value) is int and 1 <= value <= MAX_USAGE_HISTORY_BYTES_HARD_CAP


def _require_safe_config(
    *,
    stale_after_seconds: float | int,
    usage_history_limit: int,
    usage_history_max_bytes: int,
    now: Any,
) -> None:
    if not _stale_after_valid(stale_after_seconds):
        raise ValueError("stale_after_seconds must be a finite non-negative number")
    if not _usage_history_valid(usage_history_limit):
        raise ValueError("usage_history_limit must be an exact int within the hard cap")
    if not _usage_bytes_valid(usage_history_max_bytes):
        raise ValueError("usage_history_max_bytes must be an exact int within the hard cap")
    if now is not None and not callable(now):
        raise TypeError("now must be callable or None")
