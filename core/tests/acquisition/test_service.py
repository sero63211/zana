"""Acquisition service concurrency and unsupported-runtime tests."""

from __future__ import annotations

import threading
import traceback

import pytest

from tests.acquisition.conftest import (
    BlockingStreamTransport,
    CountingLock,
    FakeAdmission,
    FakeStreamTransport,
    allowed_admission,
)
from zana_core.acquisition.endpoints import EndpointError
from zana_core.acquisition.limits import AcquisitionLimits
from zana_core.acquisition.models import (
    AcquisitionKind,
    AcquisitionPolicy,
    AcquisitionState,
    NativeAcquisitionRequest,
    NativeAcquisitionResult,
    UnsupportedRuntimeResult,
)
from zana_core.acquisition.service import (
    AcquisitionLockExhaustedError,
    AcquisitionReleaseError,
    AcquisitionService,
)


def request() -> NativeAcquisitionRequest:
    return NativeAcquisitionRequest(
        kind=AcquisitionKind.OLLAMA_PULL,
        endpoint="http://127.0.0.1:11434",
        model_reference="model",
        expected_size_bytes=100,
        user_approved=True,
    )


class TestAcquisitionService:
    def test_concurrency_cap_blocks_second_acquisition(self) -> None:
        lock = CountingLock(max_concurrent=1)
        assert lock.acquire() is True
        assert lock.acquire() is False
        lock.release()

    def test_unsupported_runtime_returns_actionable_instructions(self) -> None:
        result = AcquisitionService().acquire(
            NativeAcquisitionRequest(
                kind=AcquisitionKind.UNSUPPORTED,
                endpoint="http://127.0.0.1:11434",
                model_reference="x",
            ),
            transport=FakeStreamTransport(),
            admission=FakeAdmission(allowed_admission()),
        )
        assert hasattr(result, "native_instructions")
        assert "refresh_discovery" in result.actions

    def test_remote_denial_is_failed_not_fake_success(self) -> None:
        result = AcquisitionService().acquire(
            NativeAcquisitionRequest(
                kind=AcquisitionKind.OLLAMA_PULL,
                endpoint="http://example.com:11434",
                model_reference="x",
                expected_size_bytes=100,
                user_approved=True,
                policy=AcquisitionPolicy.EXPLICIT_REMOTE_ALLOWED,
            ),
            transport=FakeStreamTransport(),
            admission=FakeAdmission(allowed_admission()),
        )
        assert result.state == AcquisitionState.FAILED

    def test_admission_is_called_exactly_once(self) -> None:
        calls: list[int] = []

        class CountingAdmission:
            def admit(self, request):  # noqa: ANN001
                calls.append(1)
                return allowed_admission()

        transport = FakeStreamTransport(
            [
                b'{"status":"success"}\n',
            ]
        )
        result = AcquisitionService().acquire(
            request(),
            transport=transport,
            admission=CountingAdmission(),
        )
        assert result.state == AcquisitionState.SUCCEEDED
        assert len(calls) == 1

    def test_model_reference_byte_limit_at_service_boundary(self) -> None:
        service = AcquisitionService(limits=AcquisitionLimits(max_model_reference_bytes=5))
        result = service.acquire(
            NativeAcquisitionRequest(
                kind=AcquisitionKind.OLLAMA_PULL,
                endpoint="http://127.0.0.1:11434",
                model_reference="abcdef",
                expected_size_bytes=100,
                user_approved=True,
            ),
            transport=FakeStreamTransport(),
            admission=FakeAdmission(allowed_admission()),
        )
        assert result.state == AcquisitionState.FAILED
        assert result.error_code == "MODEL_REFERENCE_TOO_LONG"

    def test_default_service_lock_blocks_overlapping_second_call(self) -> None:
        gate = threading.Event()
        first_transport = BlockingStreamTransport(
            gate,
            [b'{"status":"success"}\n'],
        )
        admissions: list[int] = []

        class CountingAdmission:
            def admit(self, request):  # noqa: ANN001
                admissions.append(1)
                return allowed_admission()

        service = AcquisitionService()
        first_result: list[NativeAcquisitionResult | UnsupportedRuntimeResult] = []

        def first_call() -> None:
            first_result.append(
                service.acquire(
                    request(),
                    transport=first_transport,
                    admission=CountingAdmission(),
                )
            )

        thread = threading.Thread(target=first_call)
        thread.start()
        assert first_transport.opened.wait(timeout=3)
        assert len(admissions) == 1

        with pytest.raises(AcquisitionLockExhaustedError):
            service.acquire(
                request(),
                transport=FakeStreamTransport(),
                admission=CountingAdmission(),
            )
        assert len(admissions) == 1

        gate.set()
        thread.join(timeout=3)
        assert not thread.is_alive()
        assert first_result[0].state == AcquisitionState.SUCCEEDED

        third = service.acquire(
            request(),
            transport=FakeStreamTransport([b'{"status":"success"}\n']),
            admission=CountingAdmission(),
        )
        assert third.state == AcquisitionState.SUCCEEDED
        assert len(admissions) == 2

    def test_lock_releases_after_exception(self) -> None:
        gate = threading.Event()
        first_transport = BlockingStreamTransport(
            gate,
            raise_on_open=True,
        )
        service = AcquisitionService()
        first_result: list[NativeAcquisitionResult | UnsupportedRuntimeResult] = []

        def first_call() -> None:
            first_result.append(
                service.acquire(
                    request(),
                    transport=first_transport,
                    admission=FakeAdmission(allowed_admission()),
                )
            )

        thread = threading.Thread(target=first_call)
        thread.start()
        assert first_transport.opened.wait(timeout=3)
        with pytest.raises(AcquisitionLockExhaustedError):
            service.acquire(
                request(),
                transport=FakeStreamTransport(),
                admission=FakeAdmission(allowed_admission()),
            )
        gate.set()
        thread.join(timeout=3)
        assert not thread.is_alive()
        assert first_result[0].state == AcquisitionState.FAILED

        third = service.acquire(
            request(),
            transport=FakeStreamTransport([b'{"status":"success"}\n']),
            admission=FakeAdmission(allowed_admission()),
        )
        assert third.state == AcquisitionState.SUCCEEDED

    def test_injected_lock_can_replace_default(self) -> None:
        calls = {"acquire": 0, "release": 0}

        class SpyLock:
            def acquire(self) -> bool:
                calls["acquire"] += 1
                return True

            def release(self) -> None:
                calls["release"] += 1

        service = AcquisitionService(lock=SpyLock())
        result = service.acquire(
            request(),
            transport=FakeStreamTransport([b'{"status":"success"}\n']),
            admission=FakeAdmission(allowed_admission()),
        )
        assert result.state == AcquisitionState.SUCCEEDED
        assert calls["acquire"] == 1
        assert calls["release"] == 1

    def test_deadline_is_cumulative_through_validation(self) -> None:
        clock_values = iter([0.0, 6.0])
        service = AcquisitionService(clock=lambda: next(clock_values))
        req = request().model_copy(update={"deadline_seconds": 5.0})
        transport = FakeStreamTransport([b'{"status":"success"}\n'])
        result = service.acquire(
            req,
            transport=transport,
            admission=FakeAdmission(allowed_admission()),
        )
        assert result.state == AcquisitionState.FAILED
        assert result.error_code == "DEADLINE_EXCEEDED"
        assert transport.calls == []

    def test_remaining_deadline_is_propagated_as_timeout(self) -> None:
        clock_values = iter([0.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
        service = AcquisitionService(clock=lambda: next(clock_values))
        req = request().model_copy(update={"deadline_seconds": 12.0})
        transport = FakeStreamTransport([b'{"status":"success"}\n'])
        result = service.acquire(
            req,
            transport=transport,
            admission=FakeAdmission(allowed_admission()),
        )
        assert result.state == AcquisitionState.SUCCEEDED
        assert transport.calls[0][3] == 11.0

    def test_normalized_origin_produces_single_slash_url(self) -> None:
        service = AcquisitionService()
        transport = FakeStreamTransport([b'{"status":"success"}\n'])
        req = request().model_copy(update={"endpoint": "http://127.0.0.1:11434/"})
        result = service.acquire(
            req,
            transport=transport,
            admission=FakeAdmission(allowed_admission()),
        )
        assert result.state == AcquisitionState.SUCCEEDED
        assert transport.calls[0][1] == "http://127.0.0.1:11434/api/pull"

    def test_invalid_port_rejected_before_admission_or_transport(self) -> None:
        service = AcquisitionService()
        transport = FakeStreamTransport([b'{"status":"success"}\n'])
        admissions: list[int] = []

        class CountingAdmission:
            def admit(self, request):  # noqa: ANN001
                admissions.append(1)
                return allowed_admission()

        from zana_core.acquisition.endpoints import EndpointError

        with pytest.raises(EndpointError):
            service.acquire(
                request().model_copy(update={"endpoint": "http://127.0.0.1:0"}),
                transport=transport,
                admission=CountingAdmission(),
            )
        assert admissions == []
        assert transport.calls == []

    def test_non_numeric_port_is_generic_endpoint_error(self) -> None:
        service = AcquisitionService()
        transport = FakeStreamTransport([b'{"status":"success"}\n'])
        with pytest.raises(EndpointError) as error:
            service.acquire(
                request().model_copy(update={"endpoint": "http://127.0.0.1:notaport"}),
                transport=transport,
                admission=FakeAdmission(allowed_admission()),
            )
        assert "notaport" not in str(error.value)
        assert transport.calls == []

    def test_deadline_exhausted_by_normalization_and_admission_no_open(self) -> None:
        clock_values = iter([0.0, 6.0])
        service = AcquisitionService(clock=lambda: next(clock_values))
        req = request().model_copy(
            update={
                "endpoint": "http://127.0.0.1:11434/",
                "deadline_seconds": 5.0,
            }
        )
        transport = FakeStreamTransport([b'{"status":"success"}\n'])
        result = service.acquire(
            req,
            transport=transport,
            admission=FakeAdmission(allowed_admission()),
        )
        assert result.state == AcquisitionState.FAILED
        assert result.error_code == "DEADLINE_EXCEEDED"
        assert transport.calls == []

    def test_release_exactly_once_after_success(self) -> None:
        class StrictLock:
            acquired = 0
            released = 0

            def acquire(self) -> bool:
                self.acquired += 1
                return True

            def release(self) -> None:
                self.released += 1

        lock = StrictLock()
        service = AcquisitionService(lock=lock)
        result = service.acquire(
            request(),
            transport=FakeStreamTransport([b'{"status":"success"}\n']),
            admission=FakeAdmission(allowed_admission()),
        )
        assert result.state == AcquisitionState.SUCCEEDED
        assert lock.acquired == 1
        assert lock.released == 1

    def test_release_failure_after_success_is_canonical_cleanup_error(self) -> None:
        class FailingReleaseLock:
            def acquire(self) -> bool:
                return True

            def release(self) -> None:
                raise RuntimeError("Bearer super-secret /Users/private/model denied")

        service = AcquisitionService(lock=FailingReleaseLock())
        with pytest.raises(AcquisitionReleaseError) as error:
            service.acquire(
                request(),
                transport=FakeStreamTransport([b'{"status":"success"}\n']),
                admission=FakeAdmission(allowed_admission()),
            )
        assert "release boom" not in str(error.value)
        assert "super-secret" not in str(error.value)
        assert "/Users/private/model" not in str(error.value)
        assert str(error.value) == "Acquisition lock release failed."
        assert error.value.__cause__ is None
        formatted = "".join(traceback.format_exception(error.value))
        assert "super-secret" not in formatted
        assert "/Users/private/model" not in formatted

    def test_release_failure_after_transport_failure_is_canonical(self) -> None:
        class FailingReleaseLock:
            def acquire(self) -> bool:
                return True

            def release(self) -> None:
                raise RuntimeError("release boom")

        class RaisingOpenTransport:
            closed = False

            def open_stream(self, method, url, *, headers=None, body=None, timeout):  # noqa: ANN001
                raise RuntimeError("open boom")

            def close(self) -> None:
                self.closed = True

        service = AcquisitionService(lock=FailingReleaseLock())
        with pytest.raises(AcquisitionReleaseError):
            service.acquire(
                request(),
                transport=RaisingOpenTransport(),
                admission=FakeAdmission(allowed_admission()),
            )
