"""Disk/resource preflight with unknown-size fail-closed admission."""

from __future__ import annotations

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


def require_admission(
    provider: AdmissionProvider,
    request: NativeAcquisitionRequest,
) -> AdmissionResult:
    result = provider.admit(request)
    if not result.allowed:
        raise AdmissionDeniedError("Admission denied for resource policy reasons.")
    return result
