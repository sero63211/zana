"""Injected fake streaming transports and locks for acquisition tests."""

from __future__ import annotations

import threading
from collections.abc import Iterable, Mapping

from zana_core.acquisition.models import AdmissionResult, NativeAcquisitionRequest


class FakeStreamTransport:
    """Injected transport returning bounded raw chunks; no network used."""

    def __init__(self, chunks: Iterable[bytes] | None = None) -> None:
        self.chunks = list(chunks or [])
        self.closed = False
        self.calls: list[tuple[str, str, bytes | None, float]] = []

    def open_stream(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout: float = 30.0,
    ) -> Iterable[bytes]:
        self.calls.append((method, url, body, timeout))
        return iter(self.chunks)

    def close(self) -> None:
        self.closed = True


class BlockingStreamTransport(FakeStreamTransport):
    """Blocks in open_stream until a test gate is set; no live network."""

    def __init__(
        self,
        gate: threading.Event,
        chunks: Iterable[bytes] | None = None,
        *,
        raise_on_open: bool = False,
    ) -> None:
        super().__init__(chunks)
        self.gate = gate
        self.raise_on_open = raise_on_open
        self.opened = threading.Event()

    def open_stream(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout: float = 30.0,
    ) -> Iterable[bytes]:
        self.calls.append((method, url, body, timeout))
        self.opened.set()
        self.gate.wait(timeout=5)
        if self.raise_on_open:
            raise RuntimeError("open_stream failed")
        return iter(self.chunks)


class FakeAdmission:
    def __init__(self, result: AdmissionResult | None = None) -> None:
        self._result = result

    def admit(self, request: NativeAcquisitionRequest) -> AdmissionResult:
        return self._result or AdmissionResult(
            allowed=True, reason="ok", conservative_reserve_bytes=0
        )


class FakeCancel:
    def __init__(self, cancelled: bool = False) -> None:
        self.cancelled = cancelled

    def is_cancelled(self) -> bool:
        return self.cancelled


class CountingLock:
    def __init__(self, max_concurrent: int = 1) -> None:
        self.max_concurrent = max_concurrent
        self.active = 0

    def acquire(self) -> bool:
        if self.active >= self.max_concurrent:
            return False
        self.active += 1
        return True

    def release(self) -> None:
        self.active -= 1


def allowed_admission() -> AdmissionResult:
    return AdmissionResult(allowed=True, reason="ok", conservative_reserve_bytes=0)
