"""Synchronous, deterministic resource admission governor."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from zana_core.resources.batching import validate_batch_limits
from zana_core.resources.models import (
    HEAVY_CATEGORIES,
    AdmissionDecision,
    AdmissionOutcome,
    CategoryLimit,
    DenialReason,
    OperationCategory,
    OperationRequest,
    PlatformLabel,
    RecoveryAction,
    ResourceLease,
    ResourcePolicy,
    ResourceSnapshot,
    UsageRecord,
)
from zana_core.resources.snapshot import DefaultSnapshotProvider, SnapshotProvider

Clock = Callable[[], datetime]


class ResourceAdmissionError(Exception):
    """Raised when an operation is not admitted."""

    def __init__(self, decision: AdmissionDecision) -> None:
        self.decision = decision
        super().__init__(decision.detail or decision.reason.value)


class ResourceLeaseError(Exception):
    """Raised on double release, stale tokens, or lease misuse."""


class ResourceGovernor:
    """Admits, reserves, and releases resources synchronously.

    Expired leases are reaped only when an explicit method calls
    ``reap_expired`` (refresh/admit/release paths); no timer or background
    thread ever runs.
    """

    def __init__(
        self,
        policy: ResourcePolicy,
        provider: SnapshotProvider | None = None,
        *,
        now: Clock | None = None,
    ) -> None:
        self._policy = policy
        self._provider = provider or DefaultSnapshotProvider()
        self._now = now or (lambda: datetime.now(UTC))
        self._token_counter = 0
        self._record_counter = 0
        self._leases: dict[str, ResourceLease] = {}
        self._records: list[UsageRecord] = []
        self._expiry: dict[str, datetime] = {}
        self._active_memory = 0
        self._active_disk = 0
        self._active_items = 0
        self._active_bytes = 0
        self._active_files = 0
        self._active_workers = 0
        self._category_counts: dict[OperationCategory, int] = {}
        self._category_items: dict[OperationCategory, int] = {}
        self._category_bytes: dict[OperationCategory, int] = {}
        self._category_files: dict[OperationCategory, int] = {}
        self._snapshot_revision = 0
        self._snapshot = self._capture(0)

    @property
    def policy(self) -> ResourcePolicy:
        return self._policy

    @property
    def snapshot(self) -> ResourceSnapshot:
        return self._snapshot

    def refresh(self) -> ResourceSnapshot:
        """Reap expired leases, then capture a new snapshot revision."""
        self.reap_expired()
        self._snapshot_revision += 1
        self._snapshot = self._capture(self._snapshot_revision)
        return self._snapshot

    def _capture(self, revision: int) -> ResourceSnapshot:
        try:
            captured = self._provider.capture()
            return captured.model_copy(update={"revision": revision})
        except Exception as exc:  # noqa: BLE001 - provider failure must fail open as unknown
            return ResourceSnapshot(
                revision=revision,
                platform=PlatformLabel.UNKNOWN,
                os_name="",
                arch="",
                logical_cores=None,
                memory_total_bytes=None,
                memory_available_bytes=None,
                disk_path="",
                disk_free_bytes=None,
                probe_error=f"snapshot provider failed: {exc}",
                notes=(f"snapshot provider failed: {exc}",),
            )

    def admit(self, request: OperationRequest) -> AdmissionDecision:
        """Decide admission deterministically and reserve on ALLOW."""
        self.reap_expired()
        limit = self._policy.category_limit(request.category)
        violations = validate_batch_limits(request, limit)
        if violations and request.byte_count is not None and limit.max_bytes is not None:
            if request.byte_count > limit.max_bytes:
                return self._block(
                    request,
                    DenialReason.BYTE_LIMIT,
                    RecoveryAction.REDUCE_BATCH,
                    f"byte_count {request.byte_count} exceeds category cap {limit.max_bytes}",
                )
            return self._block(
                request,
                DenialReason.ITEM_LIMIT,
                RecoveryAction.REDUCE_BATCH,
                "; ".join(violations),
            )

        if (
            request.open_files is not None
            and request.open_files > 0
            and self._policy.max_open_files is not None
            and request.open_files > self._policy.max_open_files
        ):
            return self._block(
                request,
                DenialReason.FILE_LIMIT,
                RecoveryAction.REDUCE_BATCH,
                f"requested open_files {request.open_files} exceeds policy cap "
                f"{self._policy.max_open_files}",
            )
        if (
            request.recursion_depth is not None
            and self._policy.max_recursion_depth is not None
            and request.recursion_depth > self._policy.max_recursion_depth
        ):
            return self._block(
                request,
                DenialReason.RECURSION_LIMIT,
                RecoveryAction.REDUCE_BATCH,
                f"requested recursion_depth {request.recursion_depth} exceeds policy cap "
                f"{self._policy.max_recursion_depth}",
            )

        workers = request.requested_workers or limit.max_workers
        if request.requested_workers is not None and request.requested_workers > limit.max_workers:
            return self._block(
                request,
                DenialReason.WORKER_LIMIT,
                RecoveryAction.REDUCE_WORKERS,
                f"requested workers {request.requested_workers} exceeds category cap "
                f"{limit.max_workers}",
            )

        effective_concurrency = self._effective_concurrency(limit)
        active_count = self._category_counts.get(request.category, 0)
        if active_count >= effective_concurrency:
            return self._block(
                request,
                DenialReason.CONCURRENCY_LIMIT,
                RecoveryAction.RETRY_AFTER_RELEASE,
                f"category {request.category.value} already has {active_count} active "
                f"operations; cap is {effective_concurrency}",
            )

        if request.items_count is not None:
            active_items = self._category_items.get(request.category, 0)
            if limit.max_items is not None and request.items_count + active_items > limit.max_items:
                return self._block(
                    request,
                    DenialReason.ITEM_LIMIT,
                    RecoveryAction.REDUCE_BATCH,
                    f"items {request.items_count} plus active {active_items} exceed cap "
                    f"{limit.max_items}",
                )
        if request.byte_count is not None:
            active_bytes = self._category_bytes.get(request.category, 0)
            if limit.max_bytes is not None and request.byte_count + active_bytes > limit.max_bytes:
                return self._block(
                    request,
                    DenialReason.BYTE_LIMIT,
                    RecoveryAction.REDUCE_BATCH,
                    f"bytes {request.byte_count} plus active {active_bytes} exceed cap "
                    f"{limit.max_bytes}",
                )

        memory_decision = self._check_memory(request, limit)
        if memory_decision is not None:
            return memory_decision
        disk_decision = self._check_disk(request, limit)
        if disk_decision is not None:
            return disk_decision

        memory_bytes = request.required_memory_bytes or 0
        disk_bytes = self._disk_requirement(request, limit)
        items = request.items_count or 0
        byte_count = request.byte_count or 0
        open_files = request.open_files or 0

        self._token_counter += 1
        token = f"L-{self._token_counter:06d}"
        lease = ResourceLease(
            token=token,
            request_id=request.id,
            category=request.category,
            policy_revision=1,
            snapshot_revision=self._snapshot_revision,
            memory_bytes=memory_bytes,
            disk_bytes=disk_bytes,
            workers=workers,
            items=items,
            bytes_accounted=byte_count,
            open_files=open_files,
            active=True,
        )
        self._leases[token] = lease
        if request.ttl_seconds is not None:
            self._expiry[token] = self._now() + timedelta(seconds=request.ttl_seconds)
        self._active_memory += memory_bytes
        self._active_disk += disk_bytes
        self._active_items += items
        self._active_bytes += byte_count
        self._active_files += open_files
        self._active_workers += workers
        self._category_counts[request.category] = active_count + 1
        self._category_items[request.category] = (
            self._category_items.get(request.category, 0) + items
        )
        self._category_bytes[request.category] = (
            self._category_bytes.get(request.category, 0) + byte_count
        )
        self._category_files[request.category] = (
            self._category_files.get(request.category, 0) + open_files
        )
        self._append_record(lease, released=False)
        return AdmissionDecision(
            request_id=request.id,
            category=request.category,
            outcome=AdmissionOutcome.ALLOW,
            reason=DenialReason.NONE,
            recovery=RecoveryAction.NONE,
            detail="admitted",
            snapshot_revision=self._snapshot_revision,
            lease=lease,
        )

    def _check_memory(
        self, request: OperationRequest, limit: CategoryLimit
    ) -> AdmissionDecision | None:
        if request.required_memory_bytes is None:
            if limit.tiny or limit.allow_unknown_size:
                return None
            return AdmissionDecision(
                request_id=request.id,
                category=request.category,
                outcome=AdmissionOutcome.ASK,
                reason=DenialReason.UNKNOWN_SIZE,
                recovery=RecoveryAction.PROVIDE_ESTIMATE,
                detail="required memory is unknown; provide an explicit estimate or approval",
                snapshot_revision=self._snapshot_revision,
            )
        if (
            limit.max_memory_bytes is not None
            and request.required_memory_bytes > limit.max_memory_bytes
        ):
            return self._block(
                request,
                DenialReason.CATEGORY_LIMIT,
                RecoveryAction.INCREASE_POLICY_LIMIT,
                f"required memory {request.required_memory_bytes} exceeds category cap "
                f"{limit.max_memory_bytes}",
            )
        budget = self._memory_budget()
        if budget is None:
            if limit.tiny or limit.allow_unknown_size:
                return None
            return AdmissionDecision(
                request_id=request.id,
                category=request.category,
                outcome=AdmissionOutcome.ASK,
                reason=DenialReason.UNKNOWN_HEADROOM,
                recovery=RecoveryAction.CHECK_SNAPSHOT,
                detail="memory headroom is unknown; cannot prove safety",
                snapshot_revision=self._snapshot_revision,
            )
        if request.required_memory_bytes + self._active_memory > budget:
            return self._block(
                request,
                DenialReason.MEMORY_INSUFFICIENT,
                RecoveryAction.WAIT_FOR_HEADROOM,
                f"required memory {request.required_memory_bytes} plus active "
                f"{self._active_memory} exceeds budget {budget}",
            )
        return None

    def _check_disk(
        self, request: OperationRequest, limit: CategoryLimit
    ) -> AdmissionDecision | None:
        if request.required_disk_bytes is None:
            if limit.tiny or limit.allow_unknown_size:
                return None
            return AdmissionDecision(
                request_id=request.id,
                category=request.category,
                outcome=AdmissionOutcome.ASK,
                reason=DenialReason.UNKNOWN_SIZE,
                recovery=RecoveryAction.PROVIDE_ESTIMATE,
                detail="required disk is unknown; provide an explicit estimate or approval",
                snapshot_revision=self._snapshot_revision,
            )
        requirement = self._disk_requirement(request, limit)
        if limit.max_disk_bytes is not None and requirement > limit.max_disk_bytes:
            return self._block(
                request,
                DenialReason.CATEGORY_LIMIT,
                RecoveryAction.INCREASE_POLICY_LIMIT,
                f"disk requirement {requirement} exceeds category cap {limit.max_disk_bytes}",
            )
        budget = self._disk_budget()
        if budget is None:
            if limit.tiny or limit.allow_unknown_size:
                return None
            return AdmissionDecision(
                request_id=request.id,
                category=request.category,
                outcome=AdmissionOutcome.ASK,
                reason=DenialReason.UNKNOWN_HEADROOM,
                recovery=RecoveryAction.CHECK_SNAPSHOT,
                detail="disk headroom is unknown; cannot prove safety",
                snapshot_revision=self._snapshot_revision,
            )
        if requirement + self._active_disk > budget:
            return self._block(
                request,
                DenialReason.DISK_INSUFFICIENT,
                RecoveryAction.FREE_DISK,
                f"disk requirement {requirement} plus active {self._active_disk} "
                f"exceeds budget {budget}",
            )
        return None

    def _disk_requirement(self, request: OperationRequest, limit: CategoryLimit) -> int:
        if request.required_disk_bytes is None:
            return 0
        del limit
        overhead = self._policy.disk_overhead_fraction
        return int(request.required_disk_bytes * (1.0 + overhead))

    def _memory_budget(self) -> int | None:
        snapshot = self._snapshot
        if snapshot.memory_available_bytes is None or snapshot.memory_total_bytes is None:
            return None
        safety = int(snapshot.memory_total_bytes * self._policy.safety_reserve_fraction)
        return max(
            0,
            snapshot.memory_available_bytes - self._policy.memory_reserve_bytes - safety,
        )

    def _disk_budget(self) -> int | None:
        if self._snapshot.disk_free_bytes is None:
            return None
        return max(0, self._snapshot.disk_free_bytes - self._policy.disk_reserve_bytes)

    def _effective_concurrency(self, limit: CategoryLimit) -> int:
        if not self._policy.auto_heavy_concurrency or limit.category not in HEAVY_CATEGORIES:
            return limit.max_concurrency
        snapshot = self._snapshot
        large = (
            snapshot.memory_total_bytes is not None
            and snapshot.memory_total_bytes >= self._policy.large_host_min_memory_bytes
            and snapshot.logical_cores is not None
            and snapshot.logical_cores >= self._policy.large_host_min_cores
        )
        host_cap = self._policy.max_heavy_concurrency if large else 1
        return min(limit.max_concurrency, host_cap)

    def _block(
        self,
        request: OperationRequest,
        reason: DenialReason,
        recovery: RecoveryAction,
        detail: str,
    ) -> AdmissionDecision:
        return AdmissionDecision(
            request_id=request.id,
            category=request.category,
            outcome=AdmissionOutcome.BLOCK,
            reason=reason,
            recovery=recovery,
            detail=detail,
            snapshot_revision=self._snapshot_revision,
        )

    def release(self, token: str) -> UsageRecord:
        """Release a lease synchronously; double release/stale tokens fail cleanly."""
        lease = self._leases.pop(token, None)
        if lease is None:
            raise ResourceLeaseError(
                f"lease {token!r} is not active; it may be stale, expired, or already released"
            )
        self._expiry.pop(token, None)
        self._active_memory -= lease.memory_bytes
        self._active_disk -= lease.disk_bytes
        self._active_items -= lease.items
        self._active_bytes -= lease.bytes_accounted
        self._active_files -= lease.open_files
        self._active_workers -= lease.workers
        self._category_counts[lease.category] = max(
            0, self._category_counts.get(lease.category, 0) - 1
        )
        self._category_items[lease.category] = max(
            0, self._category_items.get(lease.category, 0) - lease.items
        )
        self._category_bytes[lease.category] = max(
            0, self._category_bytes.get(lease.category, 0) - lease.bytes_accounted
        )
        self._category_files[lease.category] = max(
            0, self._category_files.get(lease.category, 0) - lease.open_files
        )
        return self._append_record(lease, released=True)

    def cancel(self, token: str) -> UsageRecord:
        """Cancel/release a lease; accounting is restored immediately."""
        return self.release(token)

    def reap_expired(self, now: datetime | None = None) -> tuple[UsageRecord, ...]:
        """Explicitly release leases whose TTL passed; no timer ever runs."""
        if not self._expiry:
            return ()
        current = now if now is not None else self._now()
        expired = [
            token for token, expires_at in sorted(self._expiry.items()) if expires_at <= current
        ]
        return tuple(self.release(token) for token in expired)

    def active_leases(self) -> tuple[ResourceLease, ...]:
        return tuple(sorted(self._leases.values(), key=lambda lease: lease.token))

    def usage_records(self) -> tuple[UsageRecord, ...]:
        return tuple(self._records)

    @contextmanager
    def lease(self, request: OperationRequest) -> Iterator[ResourceLease]:
        """Context manager that always releases on exit, even on exceptions."""
        decision = self.admit(request)
        if decision.outcome != AdmissionOutcome.ALLOW or decision.lease is None:
            raise ResourceAdmissionError(decision)
        try:
            yield decision.lease
        finally:
            self.release(decision.lease.token)

    def _append_record(self, lease: ResourceLease, *, released: bool) -> UsageRecord:
        self._record_counter += 1
        record = UsageRecord(
            token=lease.token,
            request_id=lease.request_id,
            category=lease.category,
            policy_revision=lease.policy_revision,
            snapshot_revision=lease.snapshot_revision,
            memory_bytes=lease.memory_bytes,
            disk_bytes=lease.disk_bytes,
            workers=lease.workers,
            items=lease.items,
            bytes_accounted=lease.bytes_accounted,
            open_files=lease.open_files,
            released=released,
            sequence=self._record_counter,
        )
        self._records.append(record)
        return record
