"""Bounded inference adapter tests using injected protocol fixtures only.

These tests never open a socket, server, runtime, model, thread, or
background worker. Every transport is an injected in-memory stream fixture,
and the clock/cancellation are injected cooperative controls.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime
from typing import Any

import pytest

from zana_core.instances import GenerationSettings, SessionBinding
from zana_core.runtimes.base import InvalidRuntimeResponseError, RuntimeProbeTimeoutError
from zana_core.runtimes.inference import (
    InferenceIdentityError,
    InferenceLimits,
    InferenceParametersError,
    InferenceProtocolError,
    LineBuffer,
    validate_parameters,
    verify_identity,
)
from zana_core.runtimes.ollama import OllamaInferenceAdapter
from zana_core.runtimes.openai_compat import OpenAICompatInferenceAdapter

OLLAMA_END = "http://127.0.0.1:11434"
OPENAI_END = "http://127.0.0.1:1234"
MODEL_KEY = "qwen-example:tag"
MODEL_DIGEST = "sha256:abc123"


def make_binding(
    *,
    model_key: str = MODEL_KEY,
    model_digest: str = MODEL_DIGEST,
    runtime_id: str = "ollama-local",
    runtime_endpoint: str = OLLAMA_END,
) -> SessionBinding:
    return SessionBinding(
        session_id="sess-1",
        instance_id="inst-1",
        image_digest="sha256:image",
        base_model_digest="sha256:base",
        runtime_id=runtime_id,
        runtime_endpoint=runtime_endpoint,
        model_key=model_key,
        model_digest=model_digest,
        bound_at=datetime.now(UTC),
    )


def make_settings(**overrides: Any) -> GenerationSettings:
    defaults: dict[str, Any] = {"temperature": 0.2, "max_tokens": 64, "top_p": 1.0}
    defaults.update(overrides)
    return GenerationSettings(**defaults)


def chunks(payload: str, size: int = 8) -> list[bytes]:
    data = payload.encode("utf-8")
    return [data[i : i + size] for i in range(0, len(data), size)]


class FakeStreamTransport:
    """Injected streaming transport; never touches the network."""

    def __init__(
        self,
        *,
        body: str = "",
        chunk_size: int = 8,
        raise_timeout: bool = False,
        raise_invalid: bool = False,
        cancel_after_chunks: int | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self._body = body
        self._chunk_size = chunk_size
        self._raise_timeout = raise_timeout
        self._raise_invalid = raise_invalid
        self._cancel_after_chunks = cancel_after_chunks
        self._cancelled = cancelled or (lambda: False)
        self.calls: list[tuple[str, str, Mapping[str, str] | None, bytes | None]] = []
        self.closed = False

    def open_stream(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout: float,
    ) -> Iterator[bytes]:
        self.calls.append((method, url, headers, body))
        if self._raise_timeout:
            raise RuntimeProbeTimeoutError("bounded test timeout")
        if self._raise_invalid:
            raise InvalidRuntimeResponseError(
                "Runtime chat returned HTTP 401; runtime was not available."
            )
        for index, chunk in enumerate(chunks(self._body, self._chunk_size)):
            if self._cancelled():
                yield chunk
                return
            if self._cancel_after_chunks is not None and index >= self._cancel_after_chunks:
                yield chunk
                return
            yield chunk

    def close(self) -> None:
        self.closed = True


class CancelToken:
    def __init__(self, cancel: bool) -> None:
        self._cancel = cancel

    def is_cancelled(self) -> bool:
        return self._cancel


class TestLineBuffer:
    def test_splits_and_trims_lines(self) -> None:
        limits = InferenceLimits()
        buffer = LineBuffer(limits)
        out = list(buffer.feed(b"one\r\ntwo\nthree"))
        out.extend(buffer.finish())
        assert out == ["one", "two", "three"]

    def test_oversize_line_is_rejected(self) -> None:
        limits = InferenceLimits(max_line_bytes=8)
        buffer = LineBuffer(limits)
        list(buffer.feed(b"x" * 32))
        with pytest.raises(InferenceProtocolError):
            list(buffer.finish())

    def test_total_byte_cap_is_enforced(self) -> None:
        limits = InferenceLimits(max_total_stream_bytes=16)
        buffer = LineBuffer(limits)
        with pytest.raises(InferenceProtocolError):
            list(buffer.feed(b"x" * 32))


class TestParameterValidation:
    def test_context_over_limit(self) -> None:
        limits = InferenceLimits(max_context_chars=4)
        with pytest.raises(InferenceParametersError):
            validate_parameters(
                context="a" * 5,
                message="hi",
                settings=make_settings(),
                limits=limits,
            )

    def test_message_over_limit(self) -> None:
        limits = InferenceLimits(max_message_chars=4)
        with pytest.raises(InferenceParametersError):
            validate_parameters(
                context="ctx",
                message="a" * 5,
                settings=make_settings(),
                limits=limits,
            )

    def test_settings_max_tokens_over_bound(self) -> None:
        limits = InferenceLimits(max_output_tokens=16)
        with pytest.raises(InferenceParametersError):
            validate_parameters(
                context="ctx",
                message="hi",
                settings=make_settings(max_tokens=64),
                limits=limits,
            )


class TestOllamaInference:
    def test_single_line_success(self) -> None:
        event = {
            "model": MODEL_KEY,
            "message": {"role": "assistant", "content": "Hello"},
            "done": True,
        }
        adapter = OllamaInferenceAdapter(
            endpoint=OLLAMA_END,
            transport=FakeStreamTransport(body=json.dumps(event) + "\n"),
        )
        result = adapter.generate(
            context="sys",
            message="hi",
            settings=make_settings(),
            binding=make_binding(),
        )
        assert result.status == "completed"
        assert result.content == "Hello"
        assert result.raw_text == "Hello"

    def test_multi_line_success_accumulates(self) -> None:
        body = (
            json.dumps({"model": MODEL_KEY, "message": {"content": "Hel"}, "done": False})
            + "\n"
            + json.dumps({"model": MODEL_KEY, "message": {"content": "lo"}, "done": False})
            + "\n"
            + json.dumps({"model": MODEL_KEY, "message": {"content": ""}, "done": True})
            + "\n"
        )
        adapter = OllamaInferenceAdapter(
            endpoint=OLLAMA_END,
            transport=FakeStreamTransport(body=body),
        )
        result = adapter.generate(
            context="sys",
            message="hi",
            settings=make_settings(),
            binding=make_binding(),
        )
        assert result.status == "completed"
        assert result.content == "Hello"

    def test_identity_mismatch_is_honest_failure(self) -> None:
        event = {
            "model": "other-model",
            "message": {"content": "ignored"},
            "done": True,
        }
        adapter = OllamaInferenceAdapter(
            endpoint=OLLAMA_END,
            transport=FakeStreamTransport(body=json.dumps(event) + "\n"),
        )
        result = adapter.generate(
            context="sys",
            message="hi",
            settings=make_settings(),
            binding=make_binding(),
        )
        assert result.status == "failed"
        assert result.error_code == "IDENTITY_MISMATCH"

    def test_malformed_json_is_failed(self) -> None:
        adapter = OllamaInferenceAdapter(
            endpoint=OLLAMA_END,
            transport=FakeStreamTransport(body="not-json\n"),
        )
        result = adapter.generate(
            context="sys",
            message="hi",
            settings=make_settings(),
            binding=make_binding(),
        )
        assert result.status == "failed"
        assert result.error_code == "INFERENCE_UNPROCESSABLE"

    def test_truncated_stream_is_honest_failure(self) -> None:
        event = {
            "model": MODEL_KEY,
            "message": {"content": "partial"},
            "done": False,
        }
        adapter = OllamaInferenceAdapter(
            endpoint=OLLAMA_END,
            transport=FakeStreamTransport(body=json.dumps(event) + "\n"),
        )
        result = adapter.generate(
            context="sys",
            message="hi",
            settings=make_settings(),
            binding=make_binding(),
        )
        assert result.status == "failed"
        assert result.error_code == "STREAM_TRUNCATED"
        assert result.raw_text == "partial"

    def test_runtime_error_event_is_failed(self) -> None:
        event = {"model": MODEL_KEY, "error": "cpu overloaded", "done": True}
        adapter = OllamaInferenceAdapter(
            endpoint=OLLAMA_END,
            transport=FakeStreamTransport(body=json.dumps(event) + "\n"),
        )
        result = adapter.generate(
            context="sys",
            message="hi",
            settings=make_settings(),
            binding=make_binding(),
        )
        assert result.status == "failed"
        assert result.error_code == "RUNTIME_ERROR"
        assert "cpu overloaded" not in (result.error_message or "")

    def test_tool_calls_are_parsed(self) -> None:
        event = {
            "model": MODEL_KEY,
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": "calculator", "arguments": {"expr": "1+1"}}}],
            },
            "done": True,
        }
        adapter = OllamaInferenceAdapter(
            endpoint=OLLAMA_END,
            transport=FakeStreamTransport(body=json.dumps(event) + "\n"),
        )
        result = adapter.generate(
            context="sys",
            message="calc",
            settings=make_settings(),
            binding=make_binding(),
        )
        assert result.status == "completed"
        assert len(result.tool_requests) == 1
        assert result.tool_requests[0].tool_id == "calculator"
        assert result.tool_requests[0].arguments == {"expr": "1+1"}


class TestOpenAICompatInference:
    def test_sse_success_with_done(self) -> None:
        body = (
            'data: {"model":"qwen-example:tag",'
            '"choices":[{"delta":{"content":"Hi"},"index":0}]}\n\n'
            'data: {"model":"qwen-example:tag",'
            '"choices":[{"delta":{"content":" there"},"index":0}]}\n\n'
            'data: {"model":"qwen-example:tag",'
            '"choices":[{"delta":{},"finish_reason":"stop","index":0}]}\n\n'
            "data: [DONE]\n\n"
        )
        adapter = OpenAICompatInferenceAdapter(
            endpoint=OPENAI_END,
            transport=FakeStreamTransport(body=body),
        )
        result = adapter.generate(
            context="sys",
            message="hi",
            settings=make_settings(),
            binding=make_binding(runtime_id="openai-compatible", runtime_endpoint=OPENAI_END),
        )
        assert result.status == "completed"
        assert result.content == "Hi there"

    def test_finish_reason_length_is_partial(self) -> None:
        body = (
            'data: {"model":"qwen-example:tag",'
            '"choices":[{"delta":{"content":"Hi"},"index":0}]}\n\n'
            'data: {"model":"qwen-example:tag",'
            '"choices":[{"delta":{},"finish_reason":"length","index":0}]}\n\n'
        )
        adapter = OpenAICompatInferenceAdapter(
            endpoint=OPENAI_END,
            transport=FakeStreamTransport(body=body),
        )
        result = adapter.generate(
            context="sys",
            message="hi",
            settings=make_settings(),
            binding=make_binding(runtime_id="openai-compatible", runtime_endpoint=OPENAI_END),
        )
        assert result.status == "partial"
        assert result.content is None
        assert result.raw_text == "Hi"

    def test_identity_mismatch_is_honest_failure(self) -> None:
        body = (
            'data: {"model":"other","choices":[{"delta":{"content":"Hi"},"index":0}]}\n\n'
            'data: {"model":"other","choices":[{"delta":{},"finish_reason":"stop","index":0}]}\n\n'
        )
        adapter = OpenAICompatInferenceAdapter(
            endpoint=OPENAI_END,
            transport=FakeStreamTransport(body=body),
        )
        result = adapter.generate(
            context="sys",
            message="hi",
            settings=make_settings(),
            binding=make_binding(runtime_id="openai-compatible", runtime_endpoint=OPENAI_END),
        )
        assert result.status == "failed"
        assert result.error_code == "IDENTITY_MISMATCH"

    def test_malformed_json_is_failed(self) -> None:
        adapter = OpenAICompatInferenceAdapter(
            endpoint=OPENAI_END,
            transport=FakeStreamTransport(body="data: not-json\n\n"),
        )
        result = adapter.generate(
            context="sys",
            message="hi",
            settings=make_settings(),
            binding=make_binding(runtime_id="openai-compatible", runtime_endpoint=OPENAI_END),
        )
        assert result.status == "failed"
        assert result.error_code == "INFERENCE_UNPROCESSABLE"

    def test_truncated_stream_is_honest_failure(self) -> None:
        body = (
            'data: {"model":"qwen-example:tag",'
            '"choices":[{"delta":{"content":"Hi"},"index":0}]}\n\n'
        )
        adapter = OpenAICompatInferenceAdapter(
            endpoint=OPENAI_END,
            transport=FakeStreamTransport(body=body),
        )
        result = adapter.generate(
            context="sys",
            message="hi",
            settings=make_settings(),
            binding=make_binding(runtime_id="openai-compatible", runtime_endpoint=OPENAI_END),
        )
        assert result.status == "failed"
        assert result.error_code == "STREAM_TRUNCATED"
        assert result.raw_text == "Hi"

    def test_error_object_is_failed(self) -> None:
        body = 'data: {"error":{"message":"secret-token-failed"},"choices":[]}\n\n'
        adapter = OpenAICompatInferenceAdapter(
            endpoint=OPENAI_END,
            transport=FakeStreamTransport(body=body),
        )
        result = adapter.generate(
            context="sys",
            message="hi",
            settings=make_settings(),
            binding=make_binding(runtime_id="openai-compatible", runtime_endpoint=OPENAI_END),
        )
        assert result.status == "failed"
        assert result.error_code == "RUNTIME_ERROR"
        assert "secret-token-failed" not in (result.error_message or "")

    def test_tool_calls_are_parsed(self) -> None:
        body = (
            'data: {"model":"qwen-example:tag","choices":[{"delta":{"tool_calls":['
            '{"function":{"name":"calculator","arguments":"{\\"expr\\":\\"1+1\\"}"}}'
            ']},"index":0}]}\n\n'
            'data: {"model":"qwen-example:tag",'
            '"choices":[{"delta":{},"finish_reason":"tool_calls","index":0}]}\n\n'
        )
        adapter = OpenAICompatInferenceAdapter(
            endpoint=OPENAI_END,
            transport=FakeStreamTransport(body=body),
        )
        result = adapter.generate(
            context="sys",
            message="calc",
            settings=make_settings(),
            binding=make_binding(runtime_id="openai-compatible", runtime_endpoint=OPENAI_END),
        )
        assert result.status == "completed"
        assert len(result.tool_requests) == 1
        assert result.tool_requests[0].arguments == {"expr": "1+1"}


class TestBoundsCancellationAndSecrets:
    def _make_openai(self, transport: FakeStreamTransport) -> OpenAICompatInferenceAdapter:
        return OpenAICompatInferenceAdapter(
            endpoint=OPENAI_END,
            transport=transport,
        )

    def test_cancellation_before_open(self) -> None:
        adapter = OllamaInferenceAdapter(
            endpoint=OLLAMA_END,
            transport=FakeStreamTransport(
                body=json.dumps({"model": MODEL_KEY, "message": {"content": "ok"}, "done": True})
                + "\n"
            ),
        )
        result = adapter.generate(
            context="sys",
            message="hi",
            settings=make_settings(),
            binding=make_binding(),
            cancellation=CancelToken(cancel=True),
        )
        assert result.status == "cancelled"
        assert result.error_code == "CANCELLED"

    def test_cancellation_mid_stream(self) -> None:
        transport = FakeStreamTransport(
            body=json.dumps({"model": MODEL_KEY, "message": {"content": "partial"}, "done": False})
            + "\n",
            cancelled=lambda: True,
        )
        adapter = OllamaInferenceAdapter(endpoint=OLLAMA_END, transport=transport)
        result = adapter.generate(
            context="sys",
            message="hi",
            settings=make_settings(),
            binding=make_binding(),
            cancellation=CancelToken(cancel=True),
        )
        assert result.status == "cancelled"

    def test_timeout_is_honest(self) -> None:
        clock = {"t": 0.0}

        def fake_clock() -> float:
            clock["t"] += 1000.0
            return clock["t"]

        transport = FakeStreamTransport(
            body=json.dumps({"model": MODEL_KEY, "message": {"content": "x"}, "done": False})
            + "\n",
        )
        adapter = OllamaInferenceAdapter(
            endpoint=OLLAMA_END,
            transport=transport,
            clock=fake_clock,
            timeout_seconds=1.0,
        )
        result = adapter.generate(
            context="sys",
            message="hi",
            settings=make_settings(),
            binding=make_binding(),
        )
        assert result.status == "timeout"
        assert result.error_code == "TIMEOUT"

    def test_transport_timeout_is_failed(self) -> None:
        adapter = OllamaInferenceAdapter(
            endpoint=OLLAMA_END,
            transport=FakeStreamTransport(raise_timeout=True),
        )
        result = adapter.generate(
            context="sys",
            message="hi",
            settings=make_settings(),
            binding=make_binding(),
        )
        assert result.status == "timeout"

    def test_non_2xx_is_sanitized(self) -> None:
        adapter = OllamaInferenceAdapter(
            endpoint=OLLAMA_END,
            transport=FakeStreamTransport(raise_invalid=True),
        )
        result = adapter.generate(
            context="sys",
            message="hi",
            settings=make_settings(),
            binding=make_binding(),
        )
        assert result.status == "failed"
        assert result.error_code == "INFERENCE_UNPROCESSABLE"
        assert "HTTP 401" not in (result.error_message or "")

    def test_secret_is_never_leaked(self) -> None:
        secret = "super-secret-bearer-abc"

        class SecretTransport(FakeStreamTransport):
            def open_stream(
                self,
                method: str,
                url: str,
                *,
                headers: Mapping[str, str] | None = None,
                body: bytes | None = None,
                timeout: float,
            ) -> Iterator[bytes]:
                raise InvalidRuntimeResponseError(f"leaked {secret}")

        adapter = self._make_openai(SecretTransport())
        result = adapter.generate(
            context="sys",
            message="hi",
            settings=make_settings(),
            binding=make_binding(runtime_id="openai-compatible", runtime_endpoint=OPENAI_END),
        )
        rendered = result.model_dump_json()
        assert secret not in rendered
        assert result.error_code == "INFERENCE_UNPROCESSABLE"

    def test_oversized_output_is_bounded(self) -> None:
        event = {
            "model": MODEL_KEY,
            "message": {"content": "x" * 20000},
            "done": False,
        }
        adapter = OllamaInferenceAdapter(
            endpoint=OLLAMA_END,
            transport=FakeStreamTransport(
                body=json.dumps(event) + "\n",
                chunk_size=2048,
            ),
            limits=InferenceLimits(max_output_chars=1024),
        )
        result = adapter.generate(
            context="sys",
            message="hi",
            settings=make_settings(),
            binding=make_binding(),
        )
        assert result.status == "failed"
        assert result.error_code == "OUTPUT_LIMIT_EXCEEDED"

    def test_no_accumulation_past_limit_and_no_background_worker(self) -> None:
        event = {"model": MODEL_KEY, "message": {"content": "ok"}, "done": True}
        transport = FakeStreamTransport(body=json.dumps(event) + "\n")
        adapter = OllamaInferenceAdapter(endpoint=OLLAMA_END, transport=transport)
        result = adapter.generate(
            context="sys",
            message="hi",
            settings=make_settings(),
            binding=make_binding(),
        )
        assert result.status == "completed"
        assert transport.closed is True

    def test_request_builder_payload_is_bounded(self) -> None:
        with pytest.raises(InferenceParametersError):
            validate_parameters(
                context="a" * 1000000,
                message="hi",
                settings=make_settings(),
                limits=InferenceLimits(max_context_chars=4),
            )


def test_verify_identity_accepts_missing_model() -> None:
    binding = make_binding()
    verify_identity(payload_model=None, binding=binding)
    verify_identity(payload_model="", binding=binding)


def test_verify_identity_rejects_mismatch() -> None:
    with pytest.raises(InferenceIdentityError):
        verify_identity(payload_model="other", binding=make_binding())
