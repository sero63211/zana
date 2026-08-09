"""Narrow injected transport, admission, and cancellation protocols."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Protocol

from zana_core.acquisition.models import AdmissionResult, NativeAcquisitionRequest


class NativeStreamTransport(Protocol):
    """Injected streaming transport; never proxies or buffers model bytes."""

    def open_stream(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout: float,
    ) -> Iterable[bytes]: ...

    def close(self) -> None: ...


class AdmissionProvider(Protocol):
    """Narrow resource admission protocol compatible with resources package."""

    def admit(self, request: NativeAcquisitionRequest) -> AdmissionResult: ...


class CancellationToken(Protocol):
    """Cooperative cancellation checked between streamed events."""

    def is_cancelled(self) -> bool: ...


class AcquisitionLock(Protocol):
    """Conservative concurrency control; no background threads."""

    def acquire(self) -> bool: ...

    def release(self) -> None: ...
