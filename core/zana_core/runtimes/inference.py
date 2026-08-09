"""Bounded local inference over the canonical runtime transport.

This module implements the instance ``InferenceAdapter`` contract with
loopback-friendly injected transports, strict limits, cooperative
cancellation, an absolute deadline, exact model identity propagation, and
sanitized errors. It never starts a runtime, model, thread, or background
worker; all parsing is synchronous and bounded.
"""

from __future__ import annotations

import json
import math
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from zana_core.runtimes.base import (
    InvalidRuntimeResponseError,
    RuntimeProbeError,
    RuntimeProbeTimeoutError,
)
from zana_core.runtimes.transport import (
    StreamTransport,
    TransportCleanupError,
    UrllibStreamTransport,
)

if TYPE_CHECKING:
    from zana_core.instances import (
        CancellationToken,
        GenerationSettings,
        InferenceResult,
        SessionBinding,
        ToolRequest,
    )


def _inference_result(
    *,
    status: Literal["completed", "partial", "failed", "cancelled", "timeout"],
    content: str | None,
    raw_text: str,
    tool_requests: tuple[Any, ...] = (),
    error_code: str | None = None,
    error_message: str | None = None,
) -> Any:
    from zana_core.instances import InferenceResult

    return InferenceResult(
        status=status,
        content=content,
        raw_text=raw_text,
        tool_requests=tool_requests,
        error_code=error_code,
        error_message=error_message,
    )


def _tool_request(tool_id: str, arguments: dict[str, Any]) -> Any:
    from zana_core.instances import ToolRequest

    return ToolRequest(tool_id=tool_id, version=1, arguments=arguments)


MAX_CONTEXT_CHARS = 256_000
MAX_MESSAGE_CHARS = 64_000
MAX_OUTPUT_CHARS = 128_000
MAX_OUTPUT_TOKENS = 4096
MAX_REQUEST_BODY_BYTES = 262_144
MAX_TOTAL_STREAM_BYTES = 1_048_576
MAX_LINE_BYTES = 262_144
MAX_EVENTS = 8192
MAX_TOOL_REQUESTS = 16
MAX_STOP_SEQUENCES = 16
MAX_STOP_SEQUENCE_BYTES = 1024
MAX_STOP_TOTAL_BYTES = 16_384
MAX_GENERATION_TIMEOUT_SECONDS = 300.0
MAX_ENDPOINT_BYTES = 4096
MAX_BEARER_TOKEN_BYTES = 4096
MAX_TOOL_ARGUMENTS_CHARS = 16_000
MAX_TOOL_ARGUMENTS_BYTES = 16_000
MAX_CONTEXT_BYTES = 262_144
MAX_MESSAGE_BYTES = 65_536


class InferenceLimits(BaseModel):
    """Conservative hard maxima for one bounded inference request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_context_chars: int = Field(
        default=MAX_CONTEXT_CHARS, strict=True, ge=1, le=MAX_CONTEXT_CHARS
    )
    max_message_chars: int = Field(
        default=MAX_MESSAGE_CHARS, strict=True, ge=1, le=MAX_MESSAGE_CHARS
    )
    max_output_chars: int = Field(default=MAX_OUTPUT_CHARS, strict=True, ge=1, le=MAX_OUTPUT_CHARS)
    max_output_tokens: int = Field(
        default=MAX_OUTPUT_TOKENS, strict=True, ge=1, le=MAX_OUTPUT_TOKENS
    )
    max_request_body_bytes: int = Field(
        default=MAX_REQUEST_BODY_BYTES, strict=True, ge=1, le=MAX_REQUEST_BODY_BYTES
    )
    max_total_stream_bytes: int = Field(
        default=MAX_TOTAL_STREAM_BYTES, strict=True, ge=1, le=MAX_TOTAL_STREAM_BYTES
    )
    max_line_bytes: int = Field(default=MAX_LINE_BYTES, strict=True, ge=1, le=MAX_LINE_BYTES)
    max_events: int = Field(default=MAX_EVENTS, strict=True, ge=1, le=MAX_EVENTS)
    max_tool_requests: int = Field(
        default=MAX_TOOL_REQUESTS, strict=True, ge=1, le=MAX_TOOL_REQUESTS
    )
    max_stop_sequences: int = Field(
        default=MAX_STOP_SEQUENCES, strict=True, ge=1, le=MAX_STOP_SEQUENCES
    )
    max_stop_sequence_bytes: int = Field(
        default=MAX_STOP_SEQUENCE_BYTES, strict=True, ge=1, le=MAX_STOP_SEQUENCE_BYTES
    )
    max_stop_total_bytes: int = Field(
        default=MAX_STOP_TOTAL_BYTES, strict=True, ge=1, le=MAX_STOP_TOTAL_BYTES
    )
    max_endpoint_bytes: int = Field(
        default=MAX_ENDPOINT_BYTES, strict=True, ge=1, le=MAX_ENDPOINT_BYTES
    )
    max_bearer_token_bytes: int = Field(
        default=MAX_BEARER_TOKEN_BYTES, strict=True, ge=1, le=MAX_BEARER_TOKEN_BYTES
    )
    max_context_bytes: int = Field(
        default=MAX_CONTEXT_BYTES, strict=True, ge=1, le=MAX_CONTEXT_BYTES
    )
    max_message_bytes: int = Field(
        default=MAX_MESSAGE_BYTES, strict=True, ge=1, le=MAX_MESSAGE_BYTES
    )
    max_tool_arguments_chars: int = Field(
        default=MAX_TOOL_ARGUMENTS_CHARS, strict=True, ge=1, le=MAX_TOOL_ARGUMENTS_CHARS
    )
    max_tool_arguments_bytes: int = Field(
        default=MAX_TOOL_ARGUMENTS_BYTES, strict=True, ge=1, le=MAX_TOOL_ARGUMENTS_BYTES
    )
    max_generation_timeout_seconds: float = Field(
        default=MAX_GENERATION_TIMEOUT_SECONDS,
        strict=True,
        gt=0,
        le=MAX_GENERATION_TIMEOUT_SECONDS,
        allow_inf_nan=False,
    )


DEFAULT_INFERENCE_LIMITS = InferenceLimits()


class InferenceProtocolError(RuntimeProbeError):
    """A streamed inference response could not be parsed or is malformed."""


class InferenceParametersError(ValueError):
    """A request exceeds the bounded inference limits before any call."""


class InferenceIdentityError(RuntimeProbeError):
    """The runtime responded for a different model identity than requested."""


class InferenceUnavailableError(RuntimeProbeError):
    """A configured inference adapter reports no usable runtime/model."""


class EngineResult(BaseModel):
    """One parsed stream event: a content delta or a terminal signal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: str
    content: str
    error_code: str | None = None
    error_message: str | None = None
    # Real ToolRequest records are validated by the final InferenceResult;
    # keeping this Any avoids a Pydantic rebuild dependency on instances.
    tool_requests: tuple[Any, ...] = ()


class _Accumulator:
    """Bounded accumulation of streamed text deltas and tool requests."""

    __slots__ = ("parts", "length", "tool_requests", "_limits")

    def __init__(self, limits: InferenceLimits) -> None:
        self.parts: list[str] = []
        self.length = 0
        self.tool_requests: list[ToolRequest] = []
        self._limits = limits

    def add(self, content: str, tool_requests: Sequence[ToolRequest] = ()) -> bool:
        """Append a delta; return False when the output cap is exceeded."""
        if content:
            self.length += len(content)
            if self.length > self._limits.max_output_chars:
                return False
            self.parts.append(content)
        for request in tool_requests:
            if len(self.tool_requests) >= self._limits.max_tool_requests:
                break
            self.tool_requests.append(request)
        return True

    def text(self) -> str:
        return "".join(self.parts)


def _fresh_limits(limits: InferenceLimits | None) -> InferenceLimits:
    if limits is None:
        return InferenceLimits()
    return InferenceLimits(**limits.model_dump())


def _isfinite(value: float) -> bool:
    return math.isfinite(value)


def validate_parameters(
    *,
    context: str,
    message: str,
    settings: GenerationSettings,
    limits: InferenceLimits,
) -> None:
    """Fail before any network call when a bounded parameter is exceeded."""
    if len(context) > limits.max_context_chars:
        raise InferenceParametersError(
            f"Context exceeds the {limits.max_context_chars}-character limit."
        )
    if len(message) > limits.max_message_chars:
        raise InferenceParametersError(
            f"Message exceeds the {limits.max_message_chars}-character limit."
        )
    if len(context.encode("utf-8")) > limits.max_context_bytes:
        raise InferenceParametersError(
            f"Context exceeds the {limits.max_context_bytes}-byte limit."
        )
    if len(message.encode("utf-8")) > limits.max_message_bytes:
        raise InferenceParametersError(
            f"Message exceeds the {limits.max_message_bytes}-byte limit."
        )
    if settings.max_tokens > limits.max_output_tokens:
        raise InferenceParametersError(
            f"Requested max_tokens exceeds the {limits.max_output_tokens} bound."
        )
    if len(settings.stop) > limits.max_stop_sequences:
        raise InferenceParametersError(
            f"Stop sequences exceed the {limits.max_stop_sequences} bound."
        )
    stop_total_bytes = 0
    for stop in settings.stop:
        if not isinstance(stop, str):
            raise InferenceParametersError("Stop sequences must be strings.")
        stop_bytes = len(stop.encode("utf-8"))
        if stop_bytes > limits.max_stop_sequence_bytes:
            raise InferenceParametersError(
                f"A stop sequence exceeds the {limits.max_stop_sequence_bytes}-byte bound."
            )
        stop_total_bytes += stop_bytes
    if stop_total_bytes > limits.max_stop_total_bytes:
        raise InferenceParametersError(
            f"Stop sequences exceed the {limits.max_stop_total_bytes}-byte total bound."
        )


def sanitized_message(error: BaseException) -> str:
    """Return a stable, non-secret description of a transport/protocol bound."""
    if isinstance(error, InferenceIdentityError):
        return "The runtime responded for a different model identity."
    if isinstance(error, RuntimeProbeTimeoutError):
        return "The inference request timed out before the runtime responded."
    if isinstance(error, InvalidRuntimeResponseError):
        return "The runtime returned an invalid or unbounded response."
    if isinstance(error, InferenceProtocolError):
        return "The runtime stream could not be parsed."
    return "The local inference request failed."


class LineBuffer:
    """Bounded line framer shared by NDJSON and SSE inference streams."""

    def __init__(self, limits: InferenceLimits) -> None:
        self.limits = limits
        self._buffer = bytearray()
        self._total = 0

    def feed(self, chunk: bytes) -> Iterator[str]:
        if not chunk:
            return
        self._total += len(chunk)
        if self._total > self.limits.max_total_stream_bytes:
            raise InferenceProtocolError("Inference stream exceeded the bounded total byte limit.")
        self._buffer.extend(chunk)
        while b"\n" in self._buffer:
            raw, self._buffer = self._buffer.split(b"\n", 1)
            line_bytes = raw.rstrip(b"\r")
            if len(line_bytes) > self.limits.max_line_bytes:
                raise InferenceProtocolError(
                    "Inference stream line exceeded the bounded line limit."
                )
            try:
                line = line_bytes.decode("utf-8")
            except UnicodeDecodeError as error:
                raise InferenceProtocolError(
                    "Inference stream contained invalid UTF-8 bytes."
                ) from error
            if line:
                yield line

    def finish(self) -> Iterator[str]:
        if not self._buffer:
            return
        raw = bytes(self._buffer).rstrip(b"\r")
        self._buffer.clear()
        if len(raw) > self.limits.max_line_bytes:
            raise InferenceProtocolError("Inference stream tail exceeded the bounded line limit.")
        try:
            line = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise InferenceProtocolError(
                "Inference stream contained invalid UTF-8 bytes."
            ) from error
        if line.strip():
            yield line


class BaseRuntimeInferenceAdapter(ABC):
    """Shared bounded orchestration implementing the instance contract.

    Subclasses supply the URL path, request headers/body, and an
    event parser returning an :class:`EngineResult`. This base owns the
    deadline, cancellation, framing, parameter validation, and result
    normalization so every provider stays bounded and honest.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        runtime_id: str | None = None,
        limits: InferenceLimits | None = None,
        transport: StreamTransport | None = None,
        clock: Callable[[], float] | None = None,
        timeout_seconds: float | None = None,
        bearer_token: str | None = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.runtime_id = runtime_id
        self.limits = _fresh_limits(limits)
        self.transport = transport or UrllibStreamTransport()
        self._clock = clock or time.monotonic
        self.timeout_seconds = timeout_seconds
        self.bearer_token = bearer_token

    def _validate_config(self) -> None:
        """Reject invalid endpoint/token/timeout before any JSON/open."""
        if not self.endpoint:
            raise InferenceParametersError("An inference endpoint is required.")
        endpoint_bytes = len(self.endpoint.encode("utf-8"))
        if endpoint_bytes > self.limits.max_endpoint_bytes:
            raise InferenceParametersError(
                f"Endpoint exceeds the {self.limits.max_endpoint_bytes}-byte limit."
            )
        parts = urlsplit(self.endpoint)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise InferenceParametersError("Inference endpoints must be http(s) URLs.")
        if parts.username or parts.password:
            raise InferenceParametersError("Embedded credentials in endpoints are rejected.")
        if self.bearer_token is not None and len(self.bearer_token.encode("utf-8")) > (
            self.limits.max_bearer_token_bytes
        ):
            raise InferenceParametersError(
                f"Bearer token exceeds the {self.limits.max_bearer_token_bytes}-byte limit."
            )
        if self.timeout_seconds is not None:
            timeout = float(self.timeout_seconds)
            if not _isfinite(timeout) or timeout <= 0:
                raise InferenceParametersError("Timeout must be finite and positive.")
            if timeout > self.limits.max_generation_timeout_seconds:
                bound = self.limits.max_generation_timeout_seconds
                raise InferenceParametersError(f"Timeout exceeds the {bound}-second bound.")

    def _verify_binding(self, binding: SessionBinding) -> None:
        """Verify adapter endpoint/runtime identity before any request."""
        if self.runtime_id != binding.runtime_id:
            raise InferenceIdentityError("adapter runtime does not match the session binding")
        if self.endpoint.rstrip("/") != binding.runtime_endpoint.rstrip("/"):
            raise InferenceIdentityError("adapter endpoint does not match the session binding")

    def generate(
        self,
        *,
        context: str,
        settings: GenerationSettings,
        binding: SessionBinding,
        message: str,
        cancellation: CancellationToken | None = None,
    ) -> InferenceResult:
        try:
            self._validate_config()
            validate_parameters(
                context=context,
                message=message,
                settings=settings,
                limits=self.limits,
            )
        except InferenceParametersError as exc:
            return self._failed_result(str(exc), "PARAMETERS_EXCEEDED")

        if cancellation is not None and cancellation.is_cancelled():
            return self._cancelled_result()

        try:
            self._verify_binding(binding)
        except InferenceIdentityError as error:
            return self._failed_result(sanitized_message(error), "IDENTITY_MISMATCH")

        self.begin_generation()
        result: InferenceResult | None = None
        try:
            url, headers, body = self.build_request(
                context=context,
                message=message,
                settings=settings,
                binding=binding,
            )
            if len(body) > self.limits.max_request_body_bytes:
                return self._failed_result(
                    "The inference request exceeded the bounded payload limit.",
                    "REQUEST_PAYLOAD_EXCEEDED",
                )
            deadline_seconds = self._resolve_deadline_seconds()
            started = self._clock()
            stream = self.transport.open_stream(
                "POST",
                url,
                headers=headers,
                body=body,
                timeout=min(deadline_seconds, self.limits.max_generation_timeout_seconds),
            )
            result = self._drain(
                stream,
                started=started,
                deadline_seconds=deadline_seconds,
                settings=settings,
                binding=binding,
                cancellation=cancellation,
            )
        except RuntimeProbeTimeoutError:
            result = self._timeout_result()
        except InferenceIdentityError as error:
            result = self._failed_result(sanitized_message(error), "IDENTITY_MISMATCH")
        except TransportCleanupError:
            result = self._failed_result(
                "The inference request cleanup failed.",
                "INFERENCE_CLEANUP_FAILED",
            )
        except (InferenceProtocolError, InvalidRuntimeResponseError, RuntimeProbeError) as error:
            result = self._failed_result(sanitized_message(error), "INFERENCE_UNPROCESSABLE")
        except Exception:  # noqa: BLE001 - adapter boundary maps to a typed result
            result = self._failed_result(
                "The local inference request failed.",
                "INFERENCE_FAILED",
            )
        try:
            self.transport.close()
        except TransportCleanupError:
            return self._failed_result(
                "The inference request cleanup failed.",
                "INFERENCE_CLEANUP_FAILED",
            )
        except Exception:  # noqa: BLE001 - cleanup must not escape
            return self._failed_result(
                "The inference request cleanup failed.",
                "INFERENCE_CLEANUP_FAILED",
            )
        return (
            result
            if result is not None
            else self._failed_result(
                "The local inference request failed.",
                "INFERENCE_FAILED",
            )
        )

    def begin_generation(self) -> None:
        """Reset per-request provider state; subclasses override when needed."""
        return None

    def _drain(
        self,
        stream: Iterable[bytes],
        *,
        started: float,
        deadline_seconds: float,
        settings: GenerationSettings,
        binding: SessionBinding,
        cancellation: CancellationToken | None,
    ) -> InferenceResult:
        buffer = LineBuffer(self.limits)
        accumulator = _Accumulator(self.limits)
        events = 0
        terminal: EngineResult | None = None
        for chunk in stream:
            if self._expired(started, deadline_seconds):
                return self._timeout_result()
            if cancellation is not None and cancellation.is_cancelled():
                return self._cancelled_result()
            for line in buffer.feed(chunk):
                events += 1
                if events > self.limits.max_events:
                    return self._failed_result(
                        "Inference stream exceeded the bounded event count.",
                        "EVENT_COUNT_EXCEEDED",
                    )
                result = self.parse_event(
                    line,
                    settings=settings,
                    binding=binding,
                )
                if result is not None:
                    if result.status in ("completed", "failed", "partial"):
                        terminal = result
                        break
                    if not accumulator.add(result.content, result.tool_requests):
                        return self._failed_result(
                            "Inference output exceeded the bounded character cap.",
                            "OUTPUT_LIMIT_EXCEEDED",
                        )
            if terminal is not None:
                break
        if terminal is None:
            for line in buffer.finish():
                if self._expired(started, deadline_seconds):
                    return self._timeout_result()
                if cancellation is not None and cancellation.is_cancelled():
                    return self._cancelled_result()
                events += 1
                if events > self.limits.max_events:
                    return self._failed_result(
                        "Inference stream exceeded the bounded event count.",
                        "EVENT_COUNT_EXCEEDED",
                    )
                result = self.parse_event(
                    line,
                    settings=settings,
                    binding=binding,
                )
                if result is not None:
                    if result.status in ("completed", "failed", "partial"):
                        terminal = result
                        break
                    if not accumulator.add(result.content, result.tool_requests):
                        return self._failed_result(
                            "Inference output exceeded the bounded character cap.",
                            "OUTPUT_LIMIT_EXCEEDED",
                        )
        if terminal is None:
            return _inference_result(
                status="failed",
                content=None,
                raw_text=accumulator.text(),
                tool_requests=tuple(accumulator.tool_requests),
                error_code="STREAM_TRUNCATED",
                error_message="The runtime stream ended before a terminal event.",
            )
        if terminal.status in ("completed", "partial") and not accumulator.add(
            terminal.content,
            terminal.tool_requests,
        ):
            return self._failed_result(
                "Inference output exceeded the bounded character cap.",
                "OUTPUT_LIMIT_EXCEEDED",
            )
        raw_text = accumulator.text()
        if terminal.status == "completed":
            return _inference_result(
                status="completed",
                content=raw_text,
                raw_text=raw_text,
                tool_requests=tuple(accumulator.tool_requests),
            )
        if terminal.status == "partial":
            return _inference_result(
                status="partial",
                content=None,
                raw_text=raw_text,
                tool_requests=tuple(accumulator.tool_requests),
                error_code=terminal.error_code or "STREAM_PARTIAL",
                error_message=terminal.error_message or "The runtime stream ended early.",
            )
        return _inference_result(
            status="failed",
            content=None,
            raw_text=raw_text,
            tool_requests=tuple(accumulator.tool_requests),
            error_code=terminal.error_code or "INFERENCE_FAILED",
            error_message=terminal.error_message or "The runtime reported a failure.",
        )

    def _expired(self, started: float, deadline_seconds: float) -> bool:
        if self.timeout_seconds is not None:
            return self._clock() - started >= self.timeout_seconds
        return self._clock() - started >= deadline_seconds

    def _resolve_deadline_seconds(self) -> float:
        if self.timeout_seconds is not None:
            return min(
                self.timeout_seconds,
                self.limits.max_generation_timeout_seconds,
            )
        return self.limits.max_generation_timeout_seconds

    @abstractmethod
    def build_request(
        self,
        *,
        context: str,
        message: str,
        settings: GenerationSettings,
        binding: SessionBinding,
    ) -> tuple[str, Mapping[str, str], bytes]:
        """Return URL, headers, and a bounded request body for one provider."""

    @abstractmethod
    def parse_event(
        self,
        line: str,
        *,
        settings: GenerationSettings,
        binding: SessionBinding,
    ) -> EngineResult | None:
        """Parse one framed event into a delta or terminal signal."""

    def _cancelled_result(self) -> InferenceResult:
        return _inference_result(
            status="cancelled",
            content=None,
            raw_text="",
            error_code="CANCELLED",
            error_message="The inference request was cancelled.",
        )

    def _timeout_result(self) -> InferenceResult:
        return _inference_result(
            status="timeout",
            content=None,
            raw_text="",
            error_code="TIMEOUT",
            error_message="The inference request exceeded its bounded deadline.",
        )

    def _failed_result(self, message: str, code: str) -> InferenceResult:
        return _inference_result(
            status="failed",
            content=None,
            raw_text="",
            error_code=code,
            error_message=message,
        )


def parse_json_line(line: str, label: str) -> dict[str, Any]:
    """Parse one strictly-object JSON stream line without exposing raw bodies."""
    try:
        payload = json.loads(line)
    except ValueError as error:
        raise InferenceProtocolError(f"{label} returned invalid JSON.") from error
    if not isinstance(payload, dict):
        raise InferenceProtocolError(f"{label} returned a non-object event.")
    return payload


def verify_identity(
    *,
    payload_model: object,
    binding: SessionBinding,
) -> None:
    """Enforce exact runtime-native model identity whenever a runtime reports it."""
    if not isinstance(payload_model, str) or not payload_model:
        return
    if payload_model != binding.runtime_model_id:
        raise InferenceIdentityError("runtime reported a different model identity")


class ToolCallLimitError(InferenceProtocolError):
    """Too many tool calls appeared in one inference response."""

    code = "TOO_MANY_TOOLS"


class ToolCallParseError(InferenceProtocolError):
    """Tool calls were malformed or incomplete and cannot be trusted."""

    code = "TOOL_CALLS_MALFORMED"


class ToolCallArgumentsError(InferenceProtocolError):
    """Tool call arguments exceeded the bounded character/byte limits."""

    code = "TOOL_ARGUMENTS_LIMIT"


def _tool_call_failure(error: ToolCallLimitError | ToolCallParseError | ToolCallArgumentsError):
    return EngineResult(
        status="failed",
        content="",
        error_code=error.code,
        error_message=(
            "Tool calls were not accepted because they were malformed or exceeded bounds."
        ),
    )


def _validate_arguments_object(arguments: Any, limits: InferenceLimits) -> dict[str, Any]:
    """Decode a complete tool-call arguments payload or fail closed."""
    if isinstance(arguments, dict):
        parsed = arguments
    elif isinstance(arguments, str) and arguments.strip():
        try:
            decoded = json.loads(arguments)
        except ValueError as error:
            raise ToolCallParseError("tool arguments were not valid JSON") from error
        if not isinstance(decoded, dict):
            raise ToolCallParseError("tool arguments were not a JSON object")
        parsed = decoded
    else:
        raise ToolCallParseError("tool arguments were empty or incomplete")
    encoded = json.dumps(parsed, separators=(",", ":")).encode("utf-8")
    if len(encoded) > limits.max_tool_arguments_bytes:
        raise ToolCallArgumentsError("tool arguments exceeded the byte limit")
    if len(encoded) > limits.max_tool_arguments_chars:
        raise ToolCallArgumentsError("tool arguments exceeded the character limit")
    return parsed


def parse_complete_tool_calls(
    tool_calls: Any,
    *,
    limits: InferenceLimits,
) -> tuple[Any, ...]:
    """Convert a complete Ollama-style tool-call list, failing closed on bounds."""
    if tool_calls is None:
        return ()
    if not isinstance(tool_calls, list):
        raise ToolCallParseError("tool calls were not a list")
    if len(tool_calls) > limits.max_tool_requests:
        raise ToolCallLimitError("tool calls exceeded the bounded count")
    requests: list[Any] = []
    for call in tool_calls:
        if not isinstance(call, dict):
            raise ToolCallParseError("a tool call was not an object")
        function = call.get("function")
        if not isinstance(function, dict):
            raise ToolCallParseError("a tool call had no function object")
        name = function.get("name")
        if not isinstance(name, str) or not name:
            raise ToolCallParseError("a tool call had no name")
        parsed = _validate_arguments_object(function.get("arguments"), limits)
        requests.append(_tool_request(name, parsed))
    return tuple(requests)


@dataclass(frozen=True)
class _ToolCallFragment:
    index: int
    call_id: str | None = None
    name: str | None = None
    arguments: str = ""


class ToolCallAccumulator:
    """Bounded accumulation of OpenAI-style fragmented tool calls."""

    def __init__(self, limits: InferenceLimits) -> None:
        self._limits = limits
        self._fragments: dict[int, _ToolCallFragment] = {}

    def add_openai_delta(self, calls: Any) -> None:
        if calls is None:
            return
        if not isinstance(calls, list):
            raise ToolCallParseError("tool calls were not a list")
        for call in calls:
            if not isinstance(call, dict):
                raise ToolCallParseError("a tool call fragment was not an object")
            index_value = call.get("index")
            if not isinstance(index_value, int) or index_value < 0:
                raise ToolCallParseError("a tool call fragment had no valid index")
            if index_value >= self._limits.max_tool_requests:
                raise ToolCallLimitError("tool calls exceeded the bounded count")
            fragment = self._fragments.get(index_value)
            if fragment is None:
                if len(self._fragments) >= self._limits.max_tool_requests:
                    raise ToolCallLimitError("tool calls exceeded the bounded count")
                fragment = _ToolCallFragment(index=index_value)
            call_id = call.get("id")
            if isinstance(call_id, str) and call_id:
                if fragment.call_id is not None and fragment.call_id != call_id:
                    raise ToolCallParseError("a tool call id changed across fragments")
                fragment = _ToolCallFragment(
                    index=index_value,
                    call_id=call_id,
                    name=fragment.name,
                    arguments=fragment.arguments,
                )
            function = call.get("function")
            if isinstance(function, dict):
                name = function.get("name")
                if isinstance(name, str) and name:
                    if fragment.name is not None and fragment.name != name:
                        raise ToolCallParseError("a tool call name changed across fragments")
                    fragment = _ToolCallFragment(
                        index=index_value,
                        call_id=fragment.call_id,
                        name=name,
                        arguments=fragment.arguments,
                    )
                arguments = function.get("arguments")
                if isinstance(arguments, str):
                    fragment = _ToolCallFragment(
                        index=index_value,
                        call_id=fragment.call_id,
                        name=fragment.name,
                        arguments=fragment.arguments + arguments,
                    )
                    argument_bytes = len(fragment.arguments.encode("utf-8"))
                    if argument_bytes > self._limits.max_tool_arguments_bytes:
                        raise ToolCallArgumentsError("tool arguments exceeded the byte limit")
                    if len(fragment.arguments) > self._limits.max_tool_arguments_chars:
                        raise ToolCallArgumentsError("tool arguments exceeded the character limit")
                elif arguments is not None:
                    raise ToolCallParseError("tool arguments fragments must be strings")
            self._fragments[index_value] = fragment

    def finish(self) -> tuple[Any, ...]:
        requests: list[Any] = []
        for index in sorted(self._fragments):
            fragment = self._fragments[index]
            if not fragment.name:
                raise ToolCallParseError("a tool call never received a name")
            if not fragment.arguments:
                raise ToolCallParseError("a tool call arguments fragment was incomplete")
            parsed = _validate_arguments_object(fragment.arguments, self._limits)
            requests.append(_tool_request(fragment.name, parsed))
        return tuple(requests)
