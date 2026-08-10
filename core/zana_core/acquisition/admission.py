"""Disk/resource preflight with unknown-size fail-closed admission."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from zana_core.acquisition.limits import MAX_ADMISSION_HEADROOM
from zana_core.acquisition.models import AdmissionResult, NativeAcquisitionRequest
from zana_core.acquisition.protocols import AdmissionProvider


class AdmissionDeniedError(ValueError):
    """Raised when admission is blocked or requires approval."""


class UnknownSizeError(ValueError):
    """Raised when expected size or headroom is unknown without approval."""


class AdmissionConfig(BaseModel):
    """Frozen validated admission policy with conservative hard bounds."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reserve_bytes: int = Field(default=1 << 30, ge=0, le=MAX_ADMISSION_HEADROOM)
    headroom_unknown: bool = False
    headroom_bytes: int | None = Field(default=None, ge=0, le=MAX_ADMISSION_HEADROOM)


class DefaultAdmissionProvider:
    """Conservative admission: unknown expected size requires approval."""

    def __init__(
        self,
        *,
        reserve_bytes: int = 1 << 30,
        headroom_unknown: bool = False,
        headroom_bytes: int | None = None,
    ) -> None:
        self._config = AdmissionConfig(
            reserve_bytes=reserve_bytes,
            headroom_unknown=headroom_unknown,
            headroom_bytes=headroom_bytes,
        )

    @property
    def config(self) -> AdmissionConfig:
        return self._config

    @property
    def reserve_bytes(self) -> int:
        return self._config.reserve_bytes

    @property
    def headroom_unknown(self) -> bool:
        return self._config.headroom_unknown

    @property
    def headroom_bytes(self) -> int | None:
        return self._config.headroom_bytes

    def admit(self, request: NativeAcquisitionRequest) -> AdmissionResult:
        reserve_bytes = self.reserve_bytes
        if self.headroom_unknown:
            return AdmissionResult(
                allowed=False,
                reason="UNKNOWN_HEADROOM",
                conservative_reserve_bytes=reserve_bytes,
                explicit_user_approval=request.user_approved,
            )
        headroom = self.headroom_bytes
        if headroom is None:
            return AdmissionResult(
                allowed=False,
                reason="HEADROOM_UNAVAILABLE",
                conservative_reserve_bytes=reserve_bytes,
            )
        expected = request.expected_size_bytes
        if expected is None:
            if request.user_approved and headroom >= reserve_bytes:
                return AdmissionResult(
                    allowed=True,
                    reason="UNKNOWN_SIZE_APPROVED_WITH_RESERVE",
                    conservative_reserve_bytes=reserve_bytes,
                    explicit_user_approval=True,
                )
            return AdmissionResult(
                allowed=False,
                reason="UNKNOWN_SIZE",
                conservative_reserve_bytes=reserve_bytes,
                explicit_user_approval=request.user_approved,
            )
        if expected + reserve_bytes > headroom:
            return AdmissionResult(
                allowed=False,
                reason="DISK_INSUFFICIENT",
                conservative_reserve_bytes=reserve_bytes,
            )
        return AdmissionResult(
            allowed=True,
            reason="ADMITTED",
            conservative_reserve_bytes=reserve_bytes,
            explicit_user_approval=request.user_approved,
        )


class FilesystemAdmissionProvider:
    """Live disk-headroom admission measured on the configured data root."""

    def __init__(
        self,
        root: str | Path,
        *,
        reserve_bytes: int = 1 << 30,
        active_bytes: Callable[[], int] | None = None,
        lease_conflict: Callable[[], bool] | None = None,
    ) -> None:
        if type(reserve_bytes) is not int:
            raise TypeError("reserve_bytes must be an int")
        if reserve_bytes < 0 or reserve_bytes > MAX_ADMISSION_HEADROOM:
            raise ValueError("reserve_bytes is out of range")
        self._root = Path(root)
        self._reserve_bytes = reserve_bytes
        self._active_bytes = active_bytes or (lambda: 0)
        self._lease_conflict = lease_conflict or (lambda: False)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def reserve_bytes(self) -> int:
        return self._reserve_bytes

    def admit(self, request: NativeAcquisitionRequest) -> AdmissionResult:
        try:
            conflict = self._lease_conflict()
            active = self._active_bytes()
        except Exception:  # noqa: BLE001 - lease probes fail closed
            conflict = False
            active = -1
        if conflict:
            return AdmissionResult(
                allowed=False,
                reason="LEASE_CONFLICT",
                conservative_reserve_bytes=self._reserve_bytes,
                explicit_user_approval=request.user_approved,
            )
        if type(active) is not int or active < 0 or active > MAX_ADMISSION_HEADROOM:
            return AdmissionResult(
                allowed=False,
                reason="HEADROOM_UNAVAILABLE",
                conservative_reserve_bytes=self._reserve_bytes,
                explicit_user_approval=request.user_approved,
            )
        try:
            usage = shutil.disk_usage(self._root)
            headroom = int(usage.free)
        except OSError:
            return AdmissionResult(
                allowed=False,
                reason="HEADROOM_UNAVAILABLE",
                conservative_reserve_bytes=self._reserve_bytes,
                explicit_user_approval=request.user_approved,
            )
        expected = request.expected_size_bytes
        if expected is None:
            return AdmissionResult(
                allowed=False,
                reason="UNKNOWN_SIZE",
                conservative_reserve_bytes=self._reserve_bytes,
                explicit_user_approval=request.user_approved,
            )
        if expected <= 0:
            return AdmissionResult(
                allowed=False,
                reason="INVALID_SIZE",
                conservative_reserve_bytes=self._reserve_bytes,
                explicit_user_approval=request.user_approved,
            )
        if expected + self._reserve_bytes + active > headroom:
            return AdmissionResult(
                allowed=False,
                reason="DISK_INSUFFICIENT",
                conservative_reserve_bytes=self._reserve_bytes,
                explicit_user_approval=request.user_approved,
            )
        return AdmissionResult(
            allowed=True,
            reason="ADMITTED",
            conservative_reserve_bytes=self._reserve_bytes,
            explicit_user_approval=request.user_approved,
        )


def require_admission(
    provider: AdmissionProvider,
    request: NativeAcquisitionRequest,
) -> AdmissionResult:
    result = provider.admit(request)
    if not result.allowed:
        raise AdmissionDeniedError("Admission denied for resource policy reasons.")
    return result
