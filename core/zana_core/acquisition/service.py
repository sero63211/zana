"""Conservative acquisition service with a real non-blocking lock."""

from __future__ import annotations

import threading
from collections.abc import Callable
from time import monotonic

from zana_core.acquisition.admission import require_admission
from zana_core.acquisition.endpoints import validate_endpoint
from zana_core.acquisition.limits import AcquisitionLimits
from zana_core.acquisition.models import (
    AcquisitionKind,
    AcquisitionState,
    NativeAcquisitionProgress,
    NativeAcquisitionRequest,
    NativeAcquisitionResult,
    UnsupportedRuntimeResult,
    unsupported_runtime_result,
)
from zana_core.acquisition.ollama import OllamaNativeAcquisitionAdapter
from zana_core.acquisition.protocols import (
    AcquisitionLock,
    AdmissionProvider,
    CancellationToken,
    NativeStreamTransport,
)


class _NonBlockingSemaphore:
    """Thread-safe synchronous non-blocking lock with no background work."""

    def __init__(self, max_concurrent: int) -> None:
        self._semaphore = threading.BoundedSemaphore(max_concurrent)

    def acquire(self) -> bool:
        return self._semaphore.acquire(blocking=False)

    def release(self) -> None:
        self._semaphore.release()


class AcquisitionReleaseError(RuntimeError):
    """Canonical cleanup failure with no request/endpoint/model leakage."""


class AcquisitionLockExhaustedError(ValueError):
    """Raised when the concurrency cap is reached."""


class AcquisitionService:
    """Runs one bounded native acquisition with no background work."""

    def __init__(
        self,
        *,
        limits: AcquisitionLimits | None = None,
        lock: AcquisitionLock | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.limits = limits or AcquisitionLimits()
        self._lock = lock or _NonBlockingSemaphore(self.limits.max_concurrent_acquisitions)
        self._clock = clock or monotonic

    def acquire(
        self,
        request: NativeAcquisitionRequest,
        *,
        transport: NativeStreamTransport,
        admission: AdmissionProvider,
        cancel: CancellationToken | None = None,
        on_progress: Callable[[NativeAcquisitionProgress, int], None] | None = None,
    ) -> NativeAcquisitionResult | UnsupportedRuntimeResult:
        if request.kind != AcquisitionKind.OLLAMA_PULL:
            return unsupported_runtime_result(request.kind.value)
        deadline = self._clock() + request.deadline_seconds
        if not self._lock.acquire():
            raise AcquisitionLockExhaustedError("Concurrent acquisition cap reached.")
        try:
            origin = validate_endpoint(request.endpoint, request.policy)
            normalized_request = NativeAcquisitionRequest(
                kind=request.kind,
                endpoint=origin,
                model_reference=request.model_reference,
                policy=request.policy,
                expected_size_bytes=request.expected_size_bytes,
                user_approved=request.user_approved,
                deadline_seconds=request.deadline_seconds,
            )
            admitted = require_admission(admission, normalized_request)
            encoded = normalized_request.model_reference.encode("utf-8")
            if len(encoded) > self.limits.max_model_reference_bytes:
                return NativeAcquisitionResult(
                    request=normalized_request,
                    state=AcquisitionState.FAILED,
                    events_consumed=0,
                    retained_events=[],
                    error_code="MODEL_REFERENCE_TOO_LONG",
                    error_message="Model reference exceeds the configured byte limit.",
                )
            adapter = OllamaNativeAcquisitionAdapter(
                limits=self.limits,
                clock=self._clock,
            )
            return adapter.run(
                normalized_request,
                transport=transport,
                admitted=admitted,
                cancel=cancel,
                deadline=deadline,
                on_progress=on_progress,
            )
        finally:
            try:
                self._lock.release()
            except Exception:  # noqa: BLE001
                raise AcquisitionReleaseError("Acquisition lock release failed.") from None
