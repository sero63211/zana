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
        ToolResult,
    )
    from zana_core.tools.models import ToolDefinition


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
MAX_TOOL_CALL_ID_CHARS = 256
MAX_TOOL_CALL_ID_BYTES = 1024
MAX_TOOL_NAME_CHARS = 256
MAX_TOOL_NAME_BYTES = 1024
MAX_PROVIDER_TOOL_NAME_CHARS = 64
MAX_PROVIDER_TOOL_NAME_BYTES = 64
MAX_TOOL_DEFINITIONS = 16
MAX_TOOL_DEFINITION_CHARS = 32_000
MAX_TOOL_DEFINITION_BYTES = 32_000
MAX_TOOL_RESULTS = 16
MAX_TOOL_RESULT_CHARS = 64_000
MAX_TOOL_RESULT_BYTES = 64_000
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
    max_tool_call_id_chars: int = Field(
        default=MAX_TOOL_CALL_ID_CHARS, strict=True, ge=1, le=MAX_TOOL_CALL_ID_CHARS
    )
    max_tool_call_id_bytes: int = Field(
        default=MAX_TOOL_CALL_ID_BYTES, strict=True, ge=1, le=MAX_TOOL_CALL_ID_BYTES
    )
    max_tool_name_chars: int = Field(
        default=MAX_TOOL_NAME_CHARS, strict=True, ge=1, le=MAX_TOOL_NAME_CHARS
    )
    max_tool_name_bytes: int = Field(
        default=MAX_TOOL_NAME_BYTES, strict=True, ge=1, le=MAX_TOOL_NAME_BYTES
    )
    max_tool_definitions: int = Field(
        default=MAX_TOOL_DEFINITIONS, strict=True, ge=1, le=MAX_TOOL_DEFINITIONS
    )
    max_tool_definition_chars: int = Field(
        default=MAX_TOOL_DEFINITION_CHARS,
        strict=True,
        ge=1,
        le=MAX_TOOL_DEFINITION_CHARS,
    )
    max_tool_definition_bytes: int = Field(
        default=MAX_TOOL_DEFINITION_BYTES,
        strict=True,
        ge=1,
        le=MAX_TOOL_DEFINITION_BYTES,
    )
    max_tool_results: int = Field(default=MAX_TOOL_RESULTS, strict=True, ge=1, le=MAX_TOOL_RESULTS)
    max_tool_result_chars: int = Field(
        default=MAX_TOOL_RESULT_CHARS, strict=True, ge=1, le=MAX_TOOL_RESULT_CHARS
    )
    max_tool_result_bytes: int = Field(
        default=MAX_TOOL_RESULT_BYTES, strict=True, ge=1, le=MAX_TOOL_RESULT_BYTES
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


class ToolDefinitionsError(InferenceParametersError):
    """Trusted tool definitions were duplicate, invalid, or oversized."""

    code = "TOOL_DEFINITIONS_INVALID"


class ToolContinuationError(InferenceParametersError):
    """A bounded tool-result continuation was malformed or out of order."""

    code = "TOOL_CONTINUATION_INVALID"


class ToolResultLimitError(InferenceParametersError):
    """A tool result exceeded the bounded size or count limits."""

    code = "TOOL_RESULT_LIMIT"


class ToolAliasError(InferenceParametersError):
    """Provider tool aliases were invalid, collided, or unknown."""

    code = "TOOL_ALIAS_INVALID"


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
        try:
            parts = urlsplit(self.endpoint)
        except ValueError as error:
            raise InferenceParametersError(
                "Inference endpoint is not a valid http(s) URL."
            ) from error
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise InferenceParametersError("Inference endpoints must be http(s) URLs.")
        if parts.username or parts.password:
            raise InferenceParametersError("Embedded credentials in endpoints are rejected.")
        if parts.query or parts.fragment:
            raise InferenceParametersError(
                "Inference endpoints must not contain query or fragment parts."
            )
        if self.bearer_token is not None:
            if not isinstance(self.bearer_token, str):
                raise InferenceParametersError("Bearer token must be an exact string.")
            if len(self.bearer_token.encode("utf-8")) > self.limits.max_bearer_token_bytes:
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
        tool_definitions: Sequence[ToolDefinition] = (),
        tool_requests: Sequence[ToolRequest] = (),
        tool_results: Sequence[ToolResult] = (),
    ) -> InferenceResult:
        try:
            self._validate_config()
            validate_parameters(
                context=context,
                message=message,
                settings=settings,
                limits=self.limits,
            )
            definitions, requests, results, canonical_to_alias = validate_tool_inputs(
                tool_definitions=tool_definitions,
                tool_requests=tool_requests,
                tool_results=tool_results,
                limits=self.limits,
            )
            alias_to_canonical = (
                {alias: canonical for canonical, alias in canonical_to_alias.items()}
                if canonical_to_alias
                else None
            )
        except InferenceParametersError as exc:
            return self._failed_result(
                str(exc),
                getattr(exc, "code", "PARAMETERS_EXCEEDED"),
            )

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
                tool_definitions=definitions,
                tool_requests=requests,
                tool_results=results,
                canonical_to_alias=canonical_to_alias,
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
                alias_to_canonical=alias_to_canonical,
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
        alias_to_canonical: Mapping[str, str] | None,
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
                    alias_to_canonical=alias_to_canonical,
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
                    alias_to_canonical=alias_to_canonical,
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
        tool_definitions: Sequence[ToolDefinition],
        tool_requests: Sequence[ToolRequest],
        tool_results: Sequence[ToolResult],
        canonical_to_alias: Mapping[str, str],
    ) -> tuple[str, Mapping[str, str], bytes]:
        """Return URL, headers, and a bounded request body for one provider."""

    @abstractmethod
    def parse_event(
        self,
        line: str,
        *,
        settings: GenerationSettings,
        binding: SessionBinding,
        alias_to_canonical: Mapping[str, str] | None,
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


def _serialize_json(payload: Any) -> str:
    try:
        return json.dumps(
            payload,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ToolDefinitionsError(
            "Tool data must be JSON-serializable plain data without non-standard floats."
        ) from error


def provider_tool_name(definition: ToolDefinition, index: int) -> str:
    """Deterministic provider-safe alias for one supplied tool definition.

    The canonical trusted id (for example ``zana.calculator``) may contain
    characters rejected by OpenAI's FunctionToolParam name contract. Each
    definition is aliased to a bounded ``[A-Za-z0-9_-]`` name and mapped back
    to the exact canonical id after parsing.
    """

    alias = f"zana_{index}"
    if len(alias) > MAX_PROVIDER_TOOL_NAME_CHARS:
        raise ToolAliasError("A provider tool alias exceeded the bounded name limit.")
    if len(alias.encode("utf-8")) > MAX_PROVIDER_TOOL_NAME_BYTES:
        raise ToolAliasError("A provider tool alias exceeded the bounded byte limit.")
    return alias


def build_provider_tool_map(
    definitions: Sequence[ToolDefinition],
) -> dict[str, str]:
    """Map canonical tool ids to request-local provider-safe aliases."""
    canonical_to_alias: dict[str, str] = {}
    alias_to_canonical: dict[str, str] = {}
    for index, definition in enumerate(definitions):
        alias = provider_tool_name(definition, index)
        if alias in alias_to_canonical:
            raise ToolAliasError("Provider tool aliases collided.")
        canonical_to_alias[definition.id] = alias
        alias_to_canonical[alias] = definition.id
    return canonical_to_alias


def native_tool_schema(definition: ToolDefinition, alias: str) -> dict[str, Any]:
    """Exact provider function schema from a trusted tool definition.

    Only the bounded provider-safe alias, description, and JSON parameters
    are serialized; version, canonical id, and code-bearing fields never
    cross the runtime boundary.
    """

    return {
        "type": "function",
        "function": {
            "name": alias,
            "description": definition.description,
            "parameters": definition.input_schema,
        },
    }


def _definition_json(definition: ToolDefinition, alias: str) -> str:
    return _serialize_json(native_tool_schema(definition, alias))


def _validate_bounded_name(
    value: object,
    label: str,
    *,
    max_chars: int,
    max_bytes: int,
    error_type: type[InferenceParametersError],
) -> str:
    if not isinstance(value, str) or not value:
        raise error_type(f"A {label} must be a non-empty string.")
    if len(value) > max_chars:
        raise error_type(f"A {label} exceeded the bounded character length.")
    if len(value.encode("utf-8")) > max_bytes:
        raise error_type(f"A {label} exceeded the bounded byte length.")
    return value


def _validate_definition_fields(
    definition: ToolDefinition,
    alias: str,
    limits: InferenceLimits,
) -> None:
    _validate_bounded_name(
        definition.id,
        "tool definition id",
        max_chars=limits.max_tool_name_chars,
        max_bytes=limits.max_tool_name_bytes,
        error_type=ToolDefinitionsError,
    )
    if not isinstance(definition.version, str) or not definition.version:
        raise ToolDefinitionsError("A tool definition version must be a non-empty string.")
    if (
        len(definition.version) > limits.max_tool_name_chars
        or len(definition.version.encode("utf-8")) > limits.max_tool_name_bytes
    ):
        raise ToolDefinitionsError("A tool definition version exceeded the bounded length.")
    if not isinstance(definition.description, str):
        raise ToolDefinitionsError("A tool definition description must be a string.")
    schema = definition.input_schema
    if not isinstance(schema, dict):
        raise ToolDefinitionsError("A tool definition input schema must be a JSON object.")
    if schema.get("type") != "object":
        raise ToolDefinitionsError(
            "A tool definition input schema must declare JSON object parameters."
        )
    serialized = _definition_json(definition, alias)
    encoded = serialized.encode("utf-8")
    if len(encoded) > limits.max_tool_definition_bytes:
        raise ToolDefinitionsError("A tool definition schema exceeded the bounded byte limit.")
    if len(serialized) > limits.max_tool_definition_chars:
        raise ToolDefinitionsError("A tool definition schema exceeded the bounded character limit.")


def _validate_tool_request(
    request: object,
    limits: InferenceLimits,
    definition_ids: set[str],
) -> None:
    from zana_core.instances.models import ToolRequest

    if not isinstance(request, ToolRequest):
        raise ToolContinuationError(
            "Prior tool requests must be exact trusted ToolRequest records."
        )
    _validate_bounded_name(
        request.tool_id,
        "prior tool request id",
        max_chars=limits.max_tool_name_chars,
        max_bytes=limits.max_tool_name_bytes,
        error_type=ToolContinuationError,
    )
    if request.tool_id not in definition_ids:
        raise ToolContinuationError("A prior tool request references an undeclared tool.")
    try:
        serialized = _serialize_json(request.arguments)
    except ToolDefinitionsError as error:
        raise ToolContinuationError(
            "Prior tool request arguments must be JSON-serializable plain data "
            "without non-standard floats."
        ) from error
    encoded = serialized.encode("utf-8")
    if len(encoded) > limits.max_tool_arguments_bytes:
        raise ToolContinuationError("Prior tool request arguments exceeded the bounded byte limit.")
    if len(serialized) > limits.max_tool_arguments_chars:
        raise ToolContinuationError(
            "Prior tool request arguments exceeded the bounded character limit."
        )


def _render_tool_result(result: object) -> str:
    try:
        return _serialize_json(result.model_dump(mode="json"))  # type: ignore[attr-defined]
    except (ToolDefinitionsError, TypeError, ValueError) as error:
        raise ToolContinuationError(
            "A tool result must be JSON-serializable plain data without non-standard floats."
        ) from error


def _validate_tool_result(result: object, limits: InferenceLimits) -> None:
    from zana_core.instances.models import ToolResult

    if not isinstance(result, ToolResult):
        raise ToolContinuationError("Tool results must be exact trusted ToolResult records.")
    _validate_bounded_name(
        result.tool_id,
        "tool result id",
        max_chars=limits.max_tool_name_chars,
        max_bytes=limits.max_tool_name_bytes,
        error_type=ToolContinuationError,
    )
    content = _render_tool_result(result)
    if len(content) > limits.max_tool_result_chars:
        raise ToolResultLimitError("A tool result exceeded the bounded character limit.")
    if len(content.encode("utf-8")) > limits.max_tool_result_bytes:
        raise ToolResultLimitError("A tool result exceeded the bounded byte limit.")


def validate_tool_inputs(
    *,
    tool_definitions: Sequence[ToolDefinition],
    tool_requests: Sequence[ToolRequest],
    tool_results: Sequence[ToolResult],
    limits: InferenceLimits,
) -> tuple[
    tuple[ToolDefinition, ...],
    tuple[ToolRequest, ...],
    tuple[ToolResult, ...],
    dict[str, str],
]:
    """Validate one bounded native tool request/continuation, failing closed."""
    from zana_core.tools.models import ToolDefinition

    if len(tool_definitions) > limits.max_tool_definitions:
        raise ToolDefinitionsError("Tool definitions exceed the bounded count.")
    definition_ids: set[str] = set()
    for index, definition in enumerate(tool_definitions):
        if not isinstance(definition, ToolDefinition):
            raise ToolDefinitionsError(
                "Tool definitions must be exact trusted ToolDefinition records."
            )
        alias = provider_tool_name(definition, index)
        _validate_definition_fields(definition, alias, limits)
        if definition.id in definition_ids:
            raise ToolDefinitionsError("Duplicate tool definitions are rejected.")
        definition_ids.add(definition.id)
    canonical_to_alias = build_provider_tool_map(tool_definitions)

    if len(tool_requests) > limits.max_tool_requests:
        raise ToolContinuationError("Prior tool requests exceed the bounded count.")
    if len(tool_results) > limits.max_tool_results:
        raise ToolResultLimitError("Tool results exceed the bounded count.")
    if tool_results and not tool_requests:
        raise ToolContinuationError("Tool results require matching prior tool requests.")
    if tool_requests:
        if not tool_definitions:
            raise ToolContinuationError(
                "Tool definitions are required for a bounded tool continuation."
            )
        if len(tool_requests) != len(tool_results):
            raise ToolContinuationError(
                "Tool results must exactly match prior tool requests in order."
            )
    for request, result in zip(tool_requests, tool_results, strict=True):
        _validate_tool_request(request, limits, definition_ids)
        _validate_tool_result(result, limits)
        if request.tool_id != result.tool_id:
            raise ToolContinuationError("Tool results must match the prior tool requests in order.")
    return tuple(tool_definitions), tuple(tool_requests), tuple(tool_results), canonical_to_alias


def _continuation_call_ids(count: int, limits: InferenceLimits) -> tuple[str, ...]:
    call_ids = tuple(f"zana-{index}" for index in range(count))
    for call_id in call_ids:
        if len(call_id) > limits.max_tool_call_id_chars:
            raise ToolContinuationError(
                "A continuation call id exceeded the bounded character limit."
            )
        if len(call_id.encode("utf-8")) > limits.max_tool_call_id_bytes:
            raise ToolContinuationError("A continuation call id exceeded the bounded byte limit.")
    return call_ids


def _build_openai_messages(
    *,
    context: str,
    message: str,
    tool_requests: Sequence[ToolRequest],
    tool_results: Sequence[ToolResult],
    canonical_to_alias: Mapping[str, str],
    limits: InferenceLimits,
) -> list[dict[str, Any]]:
    """Build canonical OpenAI assistant/tool continuation messages."""
    messages: list[dict[str, Any]] = [{"role": "system", "content": context}]
    messages.append({"role": "user", "content": message})
    if tool_requests:
        call_ids = _continuation_call_ids(len(tool_requests), limits)
        messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": call_ids[index],
                        "type": "function",
                        "function": {
                            "name": canonical_to_alias[request.tool_id],
                            "arguments": json.dumps(
                                request.arguments,
                                separators=(",", ":"),
                                ensure_ascii=False,
                                allow_nan=False,
                            ),
                        },
                    }
                    for index, request in enumerate(tool_requests)
                ],
            }
        )
        messages.extend(
            {
                "role": "tool",
                "tool_call_id": call_ids[index],
                "content": _render_tool_result(result),
            }
            for index, result in enumerate(tool_results)
        )
    return messages


def _build_ollama_messages(
    *,
    context: str,
    message: str,
    tool_requests: Sequence[ToolRequest],
    tool_results: Sequence[ToolResult],
    canonical_to_alias: Mapping[str, str],
) -> list[dict[str, Any]]:
    """Build canonical Ollama assistant/tool continuation messages."""
    messages: list[dict[str, Any]] = [{"role": "system", "content": context}]
    messages.append({"role": "user", "content": message})
    if tool_requests:
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": canonical_to_alias[request.tool_id],
                            "arguments": request.arguments,
                        }
                    }
                    for request in tool_requests
                ],
            }
        )
        messages.extend(
            {
                "role": "tool",
                "name": canonical_to_alias[result.tool_id],
                "content": _render_tool_result(result),
            }
            for result in tool_results
        )
    return messages


class ToolCallLimitError(InferenceProtocolError):
    """Too many tool calls appeared in one inference response."""

    code = "TOO_MANY_TOOLS"


class ToolCallParseError(InferenceProtocolError):
    """Tool calls were malformed or incomplete and cannot be trusted."""

    code = "TOOL_CALLS_MALFORMED"


class ToolCallArgumentsError(InferenceProtocolError):
    """Tool call arguments exceeded the bounded character/byte limits."""

    code = "TOOL_ARGUMENTS_LIMIT"


class ToolNameError(ToolCallParseError):
    """A tool call name exceeded its bounded length or was not a string."""

    code = "TOOL_NAME_LIMIT"


class ToolCallIdError(ToolCallParseError):
    """A tool call id exceeded its bounded length or was not a string."""

    code = "TOOL_CALL_ID_LIMIT"


def _tool_call_failure(
    error: (ToolCallLimitError | ToolCallParseError | ToolCallArgumentsError | ToolAliasError),
):
    return EngineResult(
        status="failed",
        content="",
        error_code=getattr(error, "code", "TOOL_CALLS_MALFORMED"),
        error_message=(
            "Tool calls were not accepted because they were malformed or exceeded bounds."
        ),
    )


def _validate_tool_name(name: Any, limits: InferenceLimits) -> str:
    if not isinstance(name, str) or not name:
        raise ToolCallParseError("a tool call name must be a non-empty string")
    if len(name) > limits.max_tool_name_chars:
        raise ToolNameError("tool call name exceeded the character limit")
    if len(name.encode("utf-8")) > limits.max_tool_name_bytes:
        raise ToolNameError("tool call name exceeded the byte limit")
    return name


def _validate_tool_call_id(call_id: Any, limits: InferenceLimits) -> str:
    if not isinstance(call_id, str) or not call_id:
        raise ToolCallParseError("a tool call id must be a non-empty string")
    if len(call_id) > limits.max_tool_call_id_chars:
        raise ToolCallIdError("tool call id exceeded the character limit")
    if len(call_id.encode("utf-8")) > limits.max_tool_call_id_bytes:
        raise ToolCallIdError("tool call id exceeded the byte limit")
    return call_id


def _validate_arguments_object(arguments: Any, limits: InferenceLimits) -> dict[str, Any]:
    """Decode a complete tool-call arguments payload or fail closed."""
    if isinstance(arguments, dict):
        parsed = arguments
    elif isinstance(arguments, str) and arguments.strip():
        try:
            decoded = json.loads(arguments, parse_constant=_reject_non_finite)
        except ValueError as error:
            raise ToolCallParseError("tool arguments were not valid JSON") from error
        if not isinstance(decoded, dict):
            raise ToolCallParseError("tool arguments were not a JSON object")
        parsed = decoded
    else:
        raise ToolCallParseError("tool arguments were empty or incomplete")
    _reject_non_finite_values(parsed)
    try:
        serialized = json.dumps(
            parsed,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ToolCallParseError("tool arguments were not JSON-serializable plain data") from error
    encoded = serialized.encode("utf-8")
    if len(encoded) > limits.max_tool_arguments_bytes:
        raise ToolCallArgumentsError("tool arguments exceeded the byte limit")
    if len(serialized) > limits.max_tool_arguments_chars:
        raise ToolCallArgumentsError("tool arguments exceeded the character limit")
    return parsed


def _reject_non_finite(value: str) -> None:
    raise ToolCallParseError("tool arguments contained a non-finite number")


def _reject_non_finite_values(payload: Any) -> None:
    if isinstance(payload, float):
        if not math.isfinite(payload):
            raise ToolCallParseError("tool arguments contained a non-finite number")
        return
    if isinstance(payload, dict):
        for value in payload.values():
            _reject_non_finite_values(value)
        return
    if isinstance(payload, list):
        for value in payload:
            _reject_non_finite_values(value)
        return


def parse_complete_tool_calls(
    tool_calls: Any,
    *,
    limits: InferenceLimits,
    alias_to_canonical: Mapping[str, str] | None = None,
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
        name = _validate_tool_name(function.get("name"), limits)
        if alias_to_canonical is None or name not in alias_to_canonical:
            raise ToolAliasError("runtime requested an undeclared provider tool alias")
        name = alias_to_canonical[name]
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
            if call_id is not None:
                call_id = _validate_tool_call_id(call_id, self._limits)
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
                if name is not None:
                    name = _validate_tool_name(name, self._limits)
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

    def finish(
        self,
        *,
        alias_to_canonical: Mapping[str, str] | None = None,
    ) -> tuple[Any, ...]:
        requests: list[Any] = []
        for index in sorted(self._fragments):
            fragment = self._fragments[index]
            if not fragment.name:
                raise ToolCallParseError("a tool call never received a name")
            if not fragment.arguments:
                raise ToolCallParseError("a tool call arguments fragment was incomplete")
            name = fragment.name
            if alias_to_canonical is None or name not in alias_to_canonical:
                raise ToolAliasError("runtime requested an undeclared provider tool alias")
            name = alias_to_canonical[name]
            parsed = _validate_arguments_object(fragment.arguments, self._limits)
            requests.append(_tool_request(name, parsed))
        return tuple(requests)
