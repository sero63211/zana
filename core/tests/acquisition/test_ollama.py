"""Ollama native pull adapter streaming and no-byte-proxy tests."""

from __future__ import annotations

import json
from collections.abc import Iterable

from tests.acquisition.conftest import (
    FakeCancel,
    FakeStreamTransport,
    allowed_admission,
)
from zana_core.acquisition.limits import AcquisitionLimits
from zana_core.acquisition.models import (
    AcquisitionKind,
    AcquisitionState,
    NativeAcquisitionRequest,
)
from zana_core.acquisition.ollama import OllamaNativeAcquisitionAdapter


def request(*, expected_size: int | None = 1000, approved: bool = True) -> NativeAcquisitionRequest:
    return NativeAcquisitionRequest(
        kind=AcquisitionKind.OLLAMA_PULL,
        endpoint="http://127.0.0.1:11434",
        model_reference="qwen2.5:7b",
        expected_size_bytes=expected_size,
        user_approved=approved,
    )


def event(status: str, **extra: object) -> bytes:
    return json.dumps({"status": status, **extra}, separators=(",", ":")).encode("utf-8") + b"\n"


class TestOllamaAdapter:
    def test_posts_native_pull_and_consumes_jsonl(self) -> None:
        transport = FakeStreamTransport(
            [
                event("pulling manifest"),
                event("downloading", total=100, completed=25),
                event("success"),
            ]
        )
        adapter = OllamaNativeAcquisitionAdapter()
        result = adapter.run(
            request(),
            transport=transport,
            admitted=allowed_admission(),
        )
        assert result.state == AcquisitionState.SUCCEEDED
        assert result.events_consumed == 3
        assert result.retained_events[0].status == "pulling manifest"

    def test_no_byte_proxy_semantics(self) -> None:
        transport = FakeStreamTransport([event("success")])
        adapter = OllamaNativeAcquisitionAdapter()
        result = adapter.run(
            request(),
            transport=transport,
            admitted=allowed_admission(),
        )
        assert result.state == AcquisitionState.SUCCEEDED
        assert result.events_consumed == 1
        body = transport.calls[0][2]
        assert b'"stream":true' in body
        assert b"qwen2.5:7b" in body
        assert transport.closed is True

    def test_native_error_fails(self) -> None:
        transport = FakeStreamTransport([event("error", error="denied")])
        result = OllamaNativeAcquisitionAdapter().run(
            request(),
            transport=transport,
            admitted=allowed_admission(),
        )
        assert result.state == AcquisitionState.FAILED
        assert result.error_code == "NATIVE_ERROR"

    def test_cancellation_checked_between_events(self) -> None:
        transport = FakeStreamTransport(
            [
                event("pulling manifest"),
                event("downloading", total=100, completed=10),
                event("downloading", total=100, completed=20),
            ]
        )
        result = OllamaNativeAcquisitionAdapter().run(
            request(),
            transport=transport,
            admitted=allowed_admission(),
            cancel=FakeCancel(cancelled=True),
        )
        assert result.state == AcquisitionState.CANCELLED
        assert transport.closed is True

    def test_oversized_line_fails(self) -> None:
        transport = FakeStreamTransport([b"x" * 20_000])
        adapter = OllamaNativeAcquisitionAdapter(limits=AcquisitionLimits(max_line_bytes=1_000))
        result = adapter.run(
            request(),
            transport=transport,
            admitted=allowed_admission(),
        )
        assert result.state == AcquisitionState.FAILED
        assert result.error_code == "STREAM_MALFORMED"

    def test_malformed_line_fails(self) -> None:
        transport = FakeStreamTransport([b"not json"])
        result = OllamaNativeAcquisitionAdapter().run(
            request(),
            transport=transport,
            admitted=allowed_admission(),
        )
        assert result.state == AcquisitionState.FAILED
        assert result.error_code == "STREAM_MALFORMED"

    def test_retained_events_are_bounded(self) -> None:
        chunks = [event("downloading", total=100, completed=i) for i in range(10)]
        chunks.append(event("success"))
        transport = FakeStreamTransport(chunks)
        adapter = OllamaNativeAcquisitionAdapter(limits=AcquisitionLimits(max_retained_events=3))
        result = adapter.run(
            request(),
            transport=transport,
            admitted=allowed_admission(),
        )
        assert len(result.retained_events) == 3
        assert result.events_consumed == 11

    def test_total_event_byte_budget_fails(self) -> None:
        chunks = [
            event("downloading", total=100, completed=1),
            event("downloading", total=100, completed=2),
            event("downloading", total=100, completed=3),
        ]
        transport = FakeStreamTransport(chunks)
        adapter = OllamaNativeAcquisitionAdapter(
            limits=AcquisitionLimits(
                max_total_event_bytes=50,
                max_line_bytes=8,
            )
        )
        result = adapter.run(
            request(),
            transport=transport,
            admitted=allowed_admission(),
        )
        assert result.state == AcquisitionState.FAILED
        assert result.error_code == "STREAM_OVER_TOTAL_BUDGET"

    def test_admission_denial_blocks_before_stream(self) -> None:
        from zana_core.acquisition.models import AdmissionResult

        denied = AdmissionResult(allowed=False, reason="disk insufficient")

        transport = FakeStreamTransport()
        result = OllamaNativeAcquisitionAdapter().run(
            request(),
            transport=transport,
            admitted=denied,
        )
        assert result.state == AcquisitionState.FAILED
        assert result.error_code == "ADMISSION_DENIED"
        assert transport.calls == []

    def test_split_line_chunks_are_assembled(self) -> None:
        transport = FakeStreamTransport(
            [
                b'{"status":"downloading","total":100,"com',
                b'pleted":25}\n',
                b'{"status":"success"}\n',
            ]
        )
        result = OllamaNativeAcquisitionAdapter().run(
            request(),
            transport=transport,
            admitted=allowed_admission(),
        )
        assert result.state == AcquisitionState.SUCCEEDED
        assert result.events_consumed == 2
        assert result.retained_events[0].status == "downloading"
        assert result.retained_events[0].progress_0_1 == 0.25

    def test_multi_line_chunk_parses_multiple_events(self) -> None:
        transport = FakeStreamTransport(
            [
                event("pulling manifest") + event("downloading", total=100, completed=10),
                event("success"),
            ]
        )
        result = OllamaNativeAcquisitionAdapter().run(
            request(),
            transport=transport,
            admitted=allowed_admission(),
        )
        assert result.state == AcquisitionState.SUCCEEDED
        assert result.events_consumed == 3

    def test_chunk_larger_than_line_cap_with_many_valid_lines_succeeds(self) -> None:
        chunks = [event("downloading", total=100, completed=i % 100) for i in range(200)]
        big_chunk = b"".join(chunks)
        assert len(big_chunk) > 8 * 1024
        transport = FakeStreamTransport([big_chunk, event("success")])
        adapter = OllamaNativeAcquisitionAdapter()
        result = adapter.run(
            request(),
            transport=transport,
            admitted=allowed_admission(),
        )
        assert result.state == AcquisitionState.SUCCEEDED
        assert result.events_consumed == 201

    def test_split_tail_across_chunks_is_assembled(self) -> None:
        transport = FakeStreamTransport(
            [
                b'{"status":"downloading","total":100,"com',
                b'pleted":25}',
                b'\n{"status":"success"}\n',
            ]
        )
        result = OllamaNativeAcquisitionAdapter().run(
            request(),
            transport=transport,
            admitted=allowed_admission(),
        )
        assert result.state == AcquisitionState.SUCCEEDED
        assert result.events_consumed == 2
        assert result.retained_events[0].progress_0_1 == 0.25

    def test_oversized_unterminated_line_fails(self) -> None:
        transport = FakeStreamTransport(
            [
                b'{"status":"downloading","detail":"',
                b"x" * 2_000,
            ]
        )
        adapter = OllamaNativeAcquisitionAdapter(limits=AcquisitionLimits(max_line_bytes=500))
        result = adapter.run(
            request(),
            transport=transport,
            admitted=allowed_admission(),
        )
        assert result.state == AcquisitionState.FAILED
        assert result.error_code == "STREAM_MALFORMED"
        assert transport.closed is True

    def test_close_after_malformed_failure(self) -> None:
        transport = FakeStreamTransport([b"not json\n"])
        result = OllamaNativeAcquisitionAdapter().run(
            request(),
            transport=transport,
            admitted=allowed_admission(),
        )
        assert result.state == AcquisitionState.FAILED
        assert transport.closed is True

    def test_sequence_is_strictly_increasing(self) -> None:
        transport = FakeStreamTransport(
            [
                event("pulling manifest"),
                event("downloading", total=100, completed=10),
                event("downloading", total=100, completed=20),
                event("success"),
            ]
        )
        result = OllamaNativeAcquisitionAdapter().run(
            request(),
            transport=transport,
            admitted=allowed_admission(),
        )
        sequences = [item.sequence for item in result.retained_events]
        assert sequences == list(range(1, len(sequences) + 1))

    def test_secret_bearing_error_is_sanitized(self) -> None:
        transport = FakeStreamTransport(
            [b'{"status":"error","error":"Bearer super-secret-token denied"}\n']
        )
        result = OllamaNativeAcquisitionAdapter().run(
            request(),
            transport=transport,
            admitted=allowed_admission(),
        )
        assert result.state == AcquisitionState.FAILED
        assert "super-secret-token" not in result.error_message
        assert result.error_code == "NATIVE_ERROR"

    def test_huge_integer_progress_is_canonical_malformed(self) -> None:
        transport = FakeStreamTransport(
            [b'{"status":"downloading","total":99999999999999999999,"completed":1}\n']
        )
        result = OllamaNativeAcquisitionAdapter().run(
            request(),
            transport=transport,
            admitted=allowed_admission(),
        )
        assert result.state == AcquisitionState.FAILED
        assert result.error_code == "STREAM_ENDED_WITHOUT_SUCCESS"
        assert "Transport" not in (result.error_message or "")

    def test_completed_exceeds_total_is_canonical_malformed(self) -> None:
        transport = FakeStreamTransport([b'{"status":"downloading","total":10,"completed":11}\n'])
        result = OllamaNativeAcquisitionAdapter().run(
            request(),
            transport=transport,
            admitted=allowed_admission(),
        )
        assert result.state == AcquisitionState.FAILED
        assert result.error_code == "STREAM_MALFORMED"

    def test_emoji_status_and_digest_are_deterministically_truncated(self) -> None:
        transport = FakeStreamTransport(
            [
                b'{"status":"'
                + ("😀" * 300).encode()
                + b'","digest":"'
                + ("😀" * 300).encode()
                + b'","error":"x"}\n'
            ]
        )
        result = OllamaNativeAcquisitionAdapter().run(
            request(),
            transport=transport,
            admitted=allowed_admission(),
        )
        assert result.state == AcquisitionState.FAILED
        assert result.error_code == "NATIVE_ERROR"
        assert len(result.retained_events[0].status.encode("utf-8")) <= 1024
        assert len(result.retained_events[0].digest.encode("utf-8")) <= 1024

    def test_emoji_error_is_redacted_not_transport_failure(self) -> None:
        transport = FakeStreamTransport(
            [b'{"status":"error","error":"' + ("😀" * 300).encode() + b'"}\n']
        )
        result = OllamaNativeAcquisitionAdapter().run(
            request(),
            transport=transport,
            admitted=allowed_admission(),
        )
        assert result.state == AcquisitionState.FAILED
        assert result.error_code == "NATIVE_ERROR"
        assert result.error_message == "Native acquisition reported an error."

    def test_deadline_enforced_with_injected_clock(self) -> None:
        clock_values = iter([10.0, 20.0, 20.0, 20.0])
        adapter = OllamaNativeAcquisitionAdapter(clock=lambda: next(clock_values))
        transport = FakeStreamTransport(
            [
                event("pulling manifest"),
                event("downloading", total=100, completed=10),
                event("downloading", total=100, completed=20),
                event("success"),
            ]
        )
        request_obj = request()
        request_obj = request_obj.model_copy(update={"deadline_seconds": 5.0})
        result = adapter.run(
            request_obj,
            transport=transport,
            admitted=allowed_admission(),
        )
        assert result.state == AcquisitionState.FAILED
        assert result.error_code == "DEADLINE_EXCEEDED"
        assert transport.calls == []

    def test_timeout_is_propagated_to_transport(self) -> None:
        transport = FakeStreamTransport([event("success")])
        request_obj = request()
        request_obj = request_obj.model_copy(update={"deadline_seconds": 12.0})
        clock_values = iter([0.0, 1.0])
        OllamaNativeAcquisitionAdapter(clock=lambda: next(clock_values)).run(
            request_obj,
            transport=transport,
            admitted=allowed_admission(),
        )
        assert transport.calls[0][3] == 11.0

    def test_event_count_cap_retains_only_bounded_history(self) -> None:
        chunks = [event("downloading", total=100, completed=i) for i in range(20)]
        transport = FakeStreamTransport(chunks)
        adapter = OllamaNativeAcquisitionAdapter(
            limits=AcquisitionLimits(
                max_event_count=5,
                max_retained_events=2,
            )
        )
        result = adapter.run(
            request(),
            transport=transport,
            admitted=allowed_admission(),
        )
        assert result.state == AcquisitionState.FAILED
        assert result.error_code == "STREAM_EVENT_COUNT_EXCEEDED"
        assert len(result.retained_events) <= 2
        assert transport.closed is True

    def test_event_cap_stops_lazy_framing_before_rest_of_chunk(self) -> None:
        lines = [b'{"status":"downloading"}\n' for _ in range(2001)]
        lines.append(b'{"status":"malformed-after-cap"')
        big_chunk = b"".join(lines)
        transport = FakeStreamTransport([big_chunk])
        adapter = OllamaNativeAcquisitionAdapter()
        result = adapter.run(
            request(),
            transport=transport,
            admitted=allowed_admission(),
        )
        assert result.state == AcquisitionState.FAILED
        assert result.error_code == "STREAM_EVENT_COUNT_EXCEEDED"
        assert result.events_consumed == 2000
        assert len(result.retained_events) <= 50
        assert transport.closed is True

    def test_close_is_attempted_when_open_stream_raises(self) -> None:
        class RaisingOpen:
            closed = False

            def open_stream(
                self,
                method: str,
                url: str,
                *,
                headers=None,  # noqa: ANN001
                body: bytes | None = None,
                timeout: float,
            ) -> Iterable[bytes]:
                raise RuntimeError("open failed")

            def close(self) -> None:
                self.closed = True

        transport = RaisingOpen()
        result = OllamaNativeAcquisitionAdapter().run(
            request(),
            transport=transport,
            admitted=allowed_admission(),
        )
        assert result.state == AcquisitionState.FAILED
        assert result.error_code == "TRANSPORT_FAILED"
        assert transport.closed is True
