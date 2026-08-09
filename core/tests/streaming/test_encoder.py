"""Exact SSE framing, JSON stability, and injection defense tests."""

from __future__ import annotations

import pytest

from zana_core.streaming.encoder import (
    SSEEncoder,
    StreamEncodeError,
    StreamLimitError,
    canonical_json_bytes,
    encode_keepalive_comment,
)
from zana_core.streaming.models import ErrorMetadata, EventKind, StreamEvent, StreamLimits
from zana_core.streaming.redaction import Redactor


def event(**kwargs) -> StreamEvent:
    defaults = {"name": EventKind.TOKEN, "data": {"text": "hello"}}
    defaults.update(kwargs)
    return StreamEvent(**defaults)


class TestWireFraming:
    def test_exact_sse_chunk(self) -> None:
        chunk = SSEEncoder().encode(event(id="jobs:7", data={"text": "hello"}, retry_ms=1500))
        assert chunk == (b'id: jobs:7\nevent: token\nretry: 1500\ndata: {"text":"hello"}\n\n')

    def test_deterministic_compact_json(self) -> None:
        assert canonical_json_bytes({"z": 1, "a": "x"}) == b'{"a":"x","z":1}'
        assert SSEEncoder().encode(event(data={"b": 2, "a": 1})) == SSEEncoder().encode(
            event(data={"a": 1, "b": 2})
        )

    def test_multiline_data_uses_data_lines(self) -> None:
        chunk = SSEEncoder().encode(event(data={"text": "line1\nline2"}))
        assert b'data: {"text":"line1\\nline2"}\n' in chunk

    def test_terminal_and_error_metadata(self) -> None:
        error = ErrorMetadata(
            code="MODEL_BUSY",
            message="busy",
            recovery_action="retry",
        )
        chunk = SSEEncoder().encode(event(name=EventKind.ERROR, terminal=True, error=error))
        assert b"event: error\n" in chunk
        assert b'"code":"MODEL_BUSY"' in chunk
        assert b"data: [DONE]\n\n" in chunk

    def test_keepalive_is_explicit(self) -> None:
        assert encode_keepalive_comment("still here") == b": still here\n\n"
        with pytest.raises(StreamEncodeError):
            encode_keepalive_comment("bad\ncomment")


class TestInjectionDefense:
    @pytest.mark.parametrize("bad_id", ["a\nb", "a\rb", "a\x00b"])
    def test_control_chars_in_id_rejected(self, bad_id: str) -> None:
        with pytest.raises((StreamEncodeError, ValueError)):
            SSEEncoder().encode(event(id=bad_id))

    def test_raw_exception_never_serialized(self) -> None:
        with pytest.raises(StreamEncodeError, match="raw exceptions"):
            SSEEncoder().encode(event(data=RuntimeError("secret traceback")))

    def test_non_json_data_rejected(self) -> None:
        with pytest.raises(StreamEncodeError, match="not JSON serializable"):
            SSEEncoder().encode(event(data=object()))


class TestCaps:
    def test_data_bytes_cap(self) -> None:
        limits = StreamLimits(max_data_bytes=8)
        with pytest.raises(StreamLimitError):
            SSEEncoder(limits).encode(event(data={"text": "x" * 100}))

    def test_event_bytes_cap(self) -> None:
        limits = StreamLimits(max_event_bytes=32)
        with pytest.raises(StreamLimitError):
            SSEEncoder(limits).encode(event(data={"text": "x" * 100}))

    def test_total_bytes_cap_stops_before_cap(self) -> None:
        limits = StreamLimits(max_total_bytes=40)
        encoder = SSEEncoder(limits)
        first = encoder.encode(event(data={"i": 1}))
        assert len(first) <= 60
        with pytest.raises(StreamLimitError):
            encoder.encode(event(data={"i": 2}))

    def test_retry_cap(self) -> None:
        limits = StreamLimits(max_retry_ms=100)
        with pytest.raises(StreamLimitError):
            SSEEncoder(limits).encode(event(retry_ms=200))

    def test_keepalive_total_cap(self) -> None:
        encoder = SSEEncoder(StreamLimits(max_total_bytes=10))
        with pytest.raises(StreamLimitError):
            encoder.encode_keepalive("long comment exceeds cap")


class TestRedactionIntegration:
    def test_encoder_redacts_secret_data(self) -> None:
        encoder = SSEEncoder(redactor=Redactor())
        chunk = encoder.encode(
            event(
                name=EventKind.TOOL_RESULT,
                data={"result": "ok", "authorization": "Bearer secret", "api_key": "k"},
            )
        )
        assert b"Bearer secret" not in chunk
        assert b"result" in chunk
        assert b"***" in chunk
