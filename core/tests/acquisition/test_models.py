"""Acquisition request/plan/result model tests."""

from __future__ import annotations

import pytest

from zana_core.acquisition.limits import (
    MAX_EVENT_COUNT,
    MAX_PROGRESS_VALUE,
    MAX_RETAINED_EVENTS,
)
from zana_core.acquisition.models import (
    AcquisitionKind,
    AcquisitionPolicy,
    AcquisitionState,
    AdmissionResult,
    NativeAcquisitionPlan,
    NativeAcquisitionProgress,
    NativeAcquisitionRequest,
    NativeAcquisitionResult,
    OllamaPullBody,
    UnsupportedRuntimeResult,
)


class TestRequestValidation:
    def test_rejects_shell_and_control_bytes(self) -> None:
        with pytest.raises(ValueError):
            NativeAcquisitionRequest(
                kind=AcquisitionKind.OLLAMA_PULL,
                endpoint="http://127.0.0.1:11434",
                model_reference="x\x1b",
            )
        with pytest.raises(ValueError):
            NativeAcquisitionRequest(
                kind=AcquisitionKind.OLLAMA_PULL,
                endpoint="http://127.0.0.1:11434",
                model_reference="bad\nref",
            )

    def test_bounded_reference_accepted(self) -> None:
        request = NativeAcquisitionRequest(
            kind=AcquisitionKind.OLLAMA_PULL,
            endpoint="http://127.0.0.1:11434",
            model_reference="qwen2.5:7b",
        )
        assert request.model_reference == "qwen2.5:7b"
        assert request.deadline_seconds > 0

    def test_utf8_byte_overflow_rejected(self) -> None:
        with pytest.raises(ValueError):
            NativeAcquisitionRequest(
                kind=AcquisitionKind.OLLAMA_PULL,
                endpoint="http://127.0.0.1:11434",
                model_reference="é" * 101,
            )

    def test_remote_policy_field_is_validated(self) -> None:
        request = NativeAcquisitionRequest(
            kind=AcquisitionKind.OLLAMA_PULL,
            endpoint="http://127.0.0.1:11434",
            model_reference="model",
            policy=AcquisitionPolicy.EXPLICIT_REMOTE_ALLOWED,
        )
        assert request.policy == AcquisitionPolicy.EXPLICIT_REMOTE_ALLOWED

    def test_progress_string_caps_reject_oversize(self) -> None:
        with pytest.raises(ValueError):
            NativeAcquisitionProgress(
                sequence=1,
                status="x" * 513,
                digest="d" * 513,
                error="e" * 513,
            )

    def test_progress_numeric_and_sequence_caps_reject_oversize(self) -> None:
        with pytest.raises(ValueError):
            NativeAcquisitionProgress(
                sequence=MAX_EVENT_COUNT + 1,
                status="ok",
                total=MAX_PROGRESS_VALUE + 1,
                completed=0,
            )
        with pytest.raises(ValueError):
            NativeAcquisitionProgress(
                sequence=1,
                status="ok",
                total=10,
                completed=11,
            )

    def test_result_retained_events_cap_rejects_oversize(self) -> None:
        event = NativeAcquisitionProgress(sequence=1, status="ok")
        with pytest.raises(ValueError):
            NativeAcquisitionResult(
                request=NativeAcquisitionRequest(
                    kind=AcquisitionKind.OLLAMA_PULL,
                    endpoint="http://127.0.0.1:11434",
                    model_reference="model",
                ),
                state=AcquisitionState.SUCCEEDED,
                retained_events=[event] * 60,
            )

    def test_result_count_and_error_code_caps_reject_oversize(self) -> None:
        request = NativeAcquisitionRequest(
            kind=AcquisitionKind.OLLAMA_PULL,
            endpoint="http://127.0.0.1:11434",
            model_reference="model",
        )
        with pytest.raises(ValueError):
            NativeAcquisitionResult(
                request=request,
                state=AcquisitionState.FAILED,
                events_consumed=MAX_EVENT_COUNT + 1,
            )
        with pytest.raises(ValueError):
            NativeAcquisitionResult(
                request=request,
                state=AcquisitionState.FAILED,
                error_code="E" * 65,
            )

    def test_plan_and_unsupported_model_caps_reject_oversize(self) -> None:
        with pytest.raises(ValueError):
            NativeAcquisitionPlan(
                kind=AcquisitionKind.OLLAMA_PULL,
                endpoint="http://127.0.0.1:11434",
                path="/" * 300,
                model_reference="model",
                body=OllamaPullBody(model="model", stream=True),
            )
        with pytest.raises(ValueError):
            UnsupportedRuntimeResult(
                runtime="r" * 200,
                message="x",
                native_instructions="i",
                actions=["refresh_discovery"],
            )

    def test_plan_body_and_endpoint_byte_caps_reject_oversize(self) -> None:
        with pytest.raises(ValueError):
            NativeAcquisitionPlan(
                kind=AcquisitionKind.OLLAMA_PULL,
                endpoint="http://127.0.0.1:11434",
                path="/api/pull",
                model_reference="é" * 101,
                body=OllamaPullBody(model="é" * 101),
            )
        with pytest.raises(ValueError):
            NativeAcquisitionPlan(
                kind=AcquisitionKind.OLLAMA_PULL,
                endpoint="é" * 1001,
                path="/api/pull",
                model_reference="model",
                body=OllamaPullBody(model="model"),
            )

    def test_admission_reserve_cap_rejects_oversize(self) -> None:
        with pytest.raises(ValueError):
            AdmissionResult(
                allowed=True,
                reason="ok",
                conservative_reserve_bytes=MAX_PROGRESS_VALUE + 1,
            )

    def test_retained_hard_cap_constant_is_strict(self) -> None:
        assert MAX_RETAINED_EVENTS == 50

    def test_emoji_byte_boundaries_are_enforced(self) -> None:
        with pytest.raises(ValueError):
            NativeAcquisitionProgress(
                sequence=1,
                status="😀" * 257,
            )
        with pytest.raises(ValueError):
            AdmissionResult(allowed=False, reason="😀" * 129)
        with pytest.raises(ValueError):
            UnsupportedRuntimeResult(
                runtime="😀" * 51,
                message="x",
                native_instructions="i",
                actions=["refresh_discovery"],
            )

    def test_plan_exact_literals_are_enforced(self) -> None:
        body = OllamaPullBody(model="model", stream=True)
        with pytest.raises(ValueError):
            NativeAcquisitionPlan(
                kind=AcquisitionKind.OLLAMA_PULL,
                endpoint="http://127.0.0.1:11434",
                method="GET",
                path="/api/pull",
                model_reference="model",
                body=body,
            )
        with pytest.raises(ValueError):
            NativeAcquisitionPlan(
                kind=AcquisitionKind.OLLAMA_PULL,
                endpoint="http://127.0.0.1:11434",
                method="POST",
                path="/api/tags",
                model_reference="model",
                body=body,
            )
