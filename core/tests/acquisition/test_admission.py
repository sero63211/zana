"""Unknown-size, headroom, and approval admission tests."""

from __future__ import annotations

import pytest

from zana_core.acquisition.admission import DefaultAdmissionProvider
from zana_core.acquisition.limits import MAX_ADMISSION_HEADROOM
from zana_core.acquisition.models import (
    AcquisitionKind,
    NativeAcquisitionRequest,
)


def request(*, expected: int | None, approved: bool = False) -> NativeAcquisitionRequest:
    return NativeAcquisitionRequest(
        kind=AcquisitionKind.OLLAMA_PULL,
        endpoint="http://127.0.0.1:11434",
        model_reference="model",
        expected_size_bytes=expected,
        user_approved=approved,
    )


class TestAdmission:
    def test_unknown_size_blocks_without_approval(self) -> None:
        result = DefaultAdmissionProvider(headroom_bytes=1_000).admit(request(expected=None))
        assert result.allowed is False
        assert result.reason == "UNKNOWN_SIZE"

    def test_unknown_size_allowed_with_explicit_approval_and_reserve(self) -> None:
        provider = DefaultAdmissionProvider(
            reserve_bytes=1 << 30,
            headroom_bytes=2 << 30,
        )
        result = provider.admit(request(expected=None, approved=True))
        assert result.allowed is True
        assert result.explicit_user_approval is True
        assert result.conservative_reserve_bytes == 1 << 30

    def test_known_size_with_headroom_allow(self) -> None:
        provider = DefaultAdmissionProvider(headroom_bytes=2_000, reserve_bytes=500)
        result = provider.admit(request(expected=1_000))
        assert result.allowed is True

    def test_known_size_over_headroom_blocks(self) -> None:
        provider = DefaultAdmissionProvider(headroom_bytes=1_000, reserve_bytes=500)
        result = provider.admit(request(expected=1_000))
        assert result.allowed is False

    def test_unknown_headroom_blocks_without_approval(self) -> None:
        provider = DefaultAdmissionProvider(headroom_unknown=True)
        result = provider.admit(request(expected=100))
        assert result.allowed is False

    def test_unknown_headroom_blocks_despite_approval(self) -> None:
        provider = DefaultAdmissionProvider(headroom_unknown=True)
        result = provider.admit(request(expected=None, approved=True))
        assert result.allowed is False
        assert result.reason == "UNKNOWN_HEADROOM"

    def test_reason_is_canonical_and_bounded(self) -> None:
        provider = DefaultAdmissionProvider(headroom_unknown=True)
        result = provider.admit(request(expected=None, approved=True))
        assert len(result.reason) <= 256
        assert result.reason == "UNKNOWN_HEADROOM"

    def test_reserve_and_headroom_are_hard_bounded(self) -> None:
        with pytest.raises(ValueError):
            DefaultAdmissionProvider(reserve_bytes=MAX_ADMISSION_HEADROOM + 1)
        with pytest.raises(ValueError):
            DefaultAdmissionProvider(headroom_bytes=MAX_ADMISSION_HEADROOM + 1)

    def test_config_mutation_cannot_alter_admission(self) -> None:
        provider = DefaultAdmissionProvider(
            reserve_bytes=500,
            headroom_bytes=1_000,
        )
        with pytest.raises(ValueError):
            provider.config.reserve_bytes = 1
        with pytest.raises(ValueError):
            provider.config.headroom_bytes = 2_000
        result = provider.admit(request(expected=400))
        assert result.allowed is True

    def test_config_binding_cannot_be_reassigned(self) -> None:
        provider = DefaultAdmissionProvider(
            reserve_bytes=500,
            headroom_bytes=1_000,
        )
        with pytest.raises(AttributeError):
            provider.config = None  # type: ignore[assignment]
        with pytest.raises(AttributeError):
            provider.reserve_bytes = 1  # type: ignore[misc]
        assert provider.admit(request(expected=400)).allowed is True
