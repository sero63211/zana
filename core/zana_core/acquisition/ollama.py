"""Real Ollama native pull adapter with a lazy bounded JSONL framer."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import suppress
from time import monotonic

from zana_core.acquisition.limits import AcquisitionLimits
from zana_core.acquisition.models import (
    AcquisitionKind,
    AcquisitionState,
    AdmissionResult,
    NativeAcquisitionPlan,
    NativeAcquisitionProgress,
    NativeAcquisitionRequest,
    NativeAcquisitionResult,
    OllamaPullBody,
)
from zana_core.acquisition.protocols import (
    CancellationToken,
    NativeStreamTransport,
)


class StreamLimitError(ValueError):
    """Raised when a streamed line exceeds hard caps."""


class StreamBudgetError(StreamLimitError):
    """Raised when total raw/event bytes exceed the budget."""


class StreamEventCountError(StreamLimitError):
    """Raised when the stream exceeds the event count cap."""


class StreamMalformedError(ValueError):
    """Raised when a streamed event is malformed."""


class DeadlineExceededError(ValueError):
    """Raised when the acquisition deadline passes."""


class _JsonlFramer:
    """Lazily assembles bounded JSONL lines from arbitrary chunks.

    Lines are yielded one at a time so the caller can stop after the event
    cap without prebuilding or processing the rest of a chunk.
    """

    def __init__(self, max_line_bytes: int, max_total_bytes: int) -> None:
        self.max_line_bytes = max_line_bytes
        self.max_total_bytes = max_total_bytes
        self.buffer = bytearray()
        self.total_bytes = 0

    def feed(self, chunk: bytes) -> Iterator[bytes]:
        if len(chunk) > self.max_total_bytes - self.total_bytes:
            raise StreamBudgetError("Native stream exceeded the total raw event byte budget.")
        self.total_bytes += len(chunk)
        return self._feed_lines(chunk)

    def _feed_lines(self, chunk: bytes) -> Iterator[bytes]:
        start = 0
        while True:
            newline = chunk.find(b"\n", start)
            if newline == -1:
                tail = chunk[start:]
                if tail:
                    if len(self.buffer) + len(tail) > self.max_line_bytes:
                        raise StreamLimitError(
                            "Native stream unfinished tail exceeds the byte cap."
                        )
                    self.buffer.extend(tail)
                return
            self.buffer.extend(chunk[start:newline])
            self.buffer.append(10)
            line = self._take_complete_line()
            start = newline + 1
            if line is not None:
                yield line

    def _take_complete_line(self) -> bytes | None:
        raw = bytes(self.buffer)
        if raw.endswith(b"\r\n"):
            raw = raw[:-2]
        elif raw.endswith(b"\n"):
            raw = raw[:-1]
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        self.buffer.clear()
        if not raw.strip():
            return None
        if len(raw) > self.max_line_bytes:
            raise StreamLimitError("Native stream line exceeds the byte cap.")
        return raw

    def finish(self) -> Iterator[bytes]:
        if not self.buffer.strip():
            self.buffer.clear()
            return
        raw = bytes(self.buffer)
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        self.buffer.clear()
        if raw.strip():
            if len(raw) > self.max_line_bytes:
                raise StreamLimitError("Native stream unfinished tail exceeds the byte cap.")
            yield raw


class OllamaNativeAcquisitionAdapter:
    """Posts native /api/pull and consumes bounded JSONL progress only."""

    def __init__(
        self,
        *,
        limits: AcquisitionLimits | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.limits = limits or AcquisitionLimits()
        self._clock = clock or monotonic

    def build_plan(self, request: NativeAcquisitionRequest) -> NativeAcquisitionPlan:
        return NativeAcquisitionPlan(
            kind=AcquisitionKind.OLLAMA_PULL,
            endpoint=request.endpoint,
            path="/api/pull",
            model_reference=request.model_reference,
            body=OllamaPullBody(model=request.model_reference, stream=True),
            stream=True,
        )

    def run(
        self,
        request: NativeAcquisitionRequest,
        *,
        transport: NativeStreamTransport,
        admitted: AdmissionResult,
        cancel: CancellationToken | None = None,
        deadline: float | None = None,
    ) -> NativeAcquisitionResult:
        if not admitted.allowed:
            return NativeAcquisitionResult(
                request=request,
                state=AcquisitionState.FAILED,
                events_consumed=0,
                retained_events=[],
                error_code="ADMISSION_DENIED",
            )
        absolute_deadline = (
            deadline if deadline is not None else self._clock() + request.deadline_seconds
        )
        plan = self.build_plan(request)
        url = f"{plan.endpoint}{plan.path}"
        body = json.dumps(plan.body.model_dump(), separators=(",", ":")).encode("utf-8")
        framer = _JsonlFramer(
            self.limits.max_line_bytes,
            self.limits.max_total_event_bytes,
        )
        retained: list[NativeAcquisitionProgress] = []
        consumed = 0
        sequence = 0
        attempted_open = False
        try:
            remaining = absolute_deadline - self._clock()
            if remaining <= 0:
                return NativeAcquisitionResult(
                    request=request,
                    state=AcquisitionState.FAILED,
                    events_consumed=0,
                    retained_events=[],
                    error_code="DEADLINE_EXCEEDED",
                    error_message="Acquisition deadline exceeded.",
                )
            attempted_open = True
            stream = transport.open_stream(
                "POST",
                url,
                headers={"Content-Type": "application/json"},
                body=body,
                timeout=remaining,
            )
            for raw in stream:
                if self._clock() >= absolute_deadline:
                    raise DeadlineExceededError("Acquisition deadline exceeded.")
                if cancel is not None and cancel.is_cancelled():
                    return NativeAcquisitionResult(
                        request=request,
                        state=AcquisitionState.CANCELLED,
                        events_consumed=consumed,
                        retained_events=list(retained),
                        error_code="CANCELLED",
                    )
                for line in framer.feed(raw):
                    if self._clock() >= absolute_deadline:
                        raise DeadlineExceededError("Acquisition deadline exceeded.")
                    if cancel is not None and cancel.is_cancelled():
                        return NativeAcquisitionResult(
                            request=request,
                            state=AcquisitionState.CANCELLED,
                            events_consumed=consumed,
                            retained_events=list(retained),
                            error_code="CANCELLED",
                        )
                    if consumed >= self.limits.max_event_count:
                        raise StreamEventCountError("Native stream exceeded the event count cap.")
                    progress = self._handle_line(
                        line,
                        sequence=sequence + 1,
                    )
                    if progress is None:
                        continue
                    consumed += 1
                    sequence += 1
                    retained.append(progress)
                    if len(retained) > self.limits.max_retained_events:
                        retained.pop(0)
                    terminal = self._terminal_result(
                        request,
                        consumed,
                        retained,
                        progress,
                    )
                    if terminal is not None:
                        return terminal
            for line in framer.finish():
                if self._clock() >= absolute_deadline:
                    raise DeadlineExceededError("Acquisition deadline exceeded.")
                if cancel is not None and cancel.is_cancelled():
                    return NativeAcquisitionResult(
                        request=request,
                        state=AcquisitionState.CANCELLED,
                        events_consumed=consumed,
                        retained_events=list(retained),
                        error_code="CANCELLED",
                    )
                if consumed >= self.limits.max_event_count:
                    raise StreamEventCountError("Native stream exceeded the event count cap.")
                progress = self._handle_line(
                    line,
                    sequence=sequence + 1,
                )
                if progress is None:
                    continue
                consumed += 1
                sequence += 1
                retained.append(progress)
                if len(retained) > self.limits.max_retained_events:
                    retained.pop(0)
                terminal = self._terminal_result(
                    request,
                    consumed,
                    retained,
                    progress,
                )
                if terminal is not None:
                    return terminal
            return NativeAcquisitionResult(
                request=request,
                state=AcquisitionState.FAILED,
                events_consumed=consumed,
                retained_events=list(retained),
                error_code="STREAM_ENDED_WITHOUT_SUCCESS",
                error_message="Native stream ended without a success event.",
            )
        except StreamEventCountError:
            return NativeAcquisitionResult(
                request=request,
                state=AcquisitionState.FAILED,
                events_consumed=consumed,
                retained_events=list(retained),
                error_code="STREAM_EVENT_COUNT_EXCEEDED",
                error_message="Native stream exceeded the event count cap.",
            )
        except StreamBudgetError:
            return NativeAcquisitionResult(
                request=request,
                state=AcquisitionState.FAILED,
                events_consumed=consumed,
                retained_events=list(retained),
                error_code="STREAM_OVER_TOTAL_BUDGET",
                error_message="Native stream exceeded the total event byte budget.",
            )
        except (StreamLimitError, StreamMalformedError, json.JSONDecodeError):
            return NativeAcquisitionResult(
                request=request,
                state=AcquisitionState.FAILED,
                events_consumed=consumed,
                retained_events=list(retained),
                error_code="STREAM_MALFORMED",
                error_message="Native stream contained malformed or oversized data.",
            )
        except DeadlineExceededError:
            return NativeAcquisitionResult(
                request=request,
                state=AcquisitionState.FAILED,
                events_consumed=consumed,
                retained_events=list(retained),
                error_code="DEADLINE_EXCEEDED",
                error_message="Acquisition deadline exceeded.",
            )
        except Exception:  # noqa: BLE001
            return NativeAcquisitionResult(
                request=request,
                state=AcquisitionState.FAILED,
                events_consumed=consumed,
                retained_events=list(retained),
                error_code="TRANSPORT_FAILED",
                error_message="Native acquisition transport failed.",
            )
        finally:
            if attempted_open:
                with suppress(Exception):
                    transport.close()

    def _handle_line(
        self,
        raw: bytes,
        *,
        sequence: int,
    ) -> NativeAcquisitionProgress | None:
        if not raw.strip():
            return None
        try:
            text = raw.decode("utf-8", errors="strict").strip()
        except UnicodeDecodeError as error:
            raise StreamMalformedError("Native stream line is not UTF-8.") from error
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as error:
            raise StreamMalformedError("Native stream line is not valid JSON.") from error
        if not isinstance(payload, dict):
            raise StreamMalformedError("Native stream event is not an object.")
        raw_status = payload.get("status")
        if not isinstance(raw_status, str):
            return None
        status = _bounded_text(raw_status)
        if status is None:
            return None
        digest = _bounded_text(payload.get("digest"))
        error = _redacted_error(payload.get("error"))
        total = _bounded_int(payload.get("total"))
        completed = _bounded_int(payload.get("completed"))
        progress = None
        if total is not None and completed is not None:
            if completed > total:
                raise StreamMalformedError("Native stream progress completed exceeds total.")
            if total > 0:
                progress = completed / total
        return NativeAcquisitionProgress(
            sequence=sequence,
            status=status,
            digest=digest,
            total=total,
            completed=completed,
            progress_0_1=progress,
            error=error,
        )

    def _terminal_result(
        self,
        request: NativeAcquisitionRequest,
        consumed: int,
        retained: list[NativeAcquisitionProgress],
        progress: NativeAcquisitionProgress,
    ) -> NativeAcquisitionResult | None:
        if progress.error:
            return NativeAcquisitionResult(
                request=request,
                state=AcquisitionState.FAILED,
                events_consumed=consumed,
                retained_events=list(retained),
                error_code="NATIVE_ERROR",
                error_message="Native acquisition reported an error.",
            )
        if progress.status in {"success", "completed"}:
            return NativeAcquisitionResult(
                request=request,
                state=AcquisitionState.SUCCEEDED,
                events_consumed=consumed,
                retained_events=list(retained),
            )
        return None


def _bounded_text(value: object, max_chars: int = 512, max_bytes: int = 1024) -> str | None:
    """Deterministically truncate to both char and UTF-8 byte budgets.

    A 4-byte emoji could exceed the byte budget within the char budget, so
    truncate byte-safely and never split a code point.
    """
    if not isinstance(value, str) or not value:
        return None
    if len(value) > max_chars:
        value = value[:max_chars]
    if len(value.encode("utf-8")) <= max_bytes:
        return value
    encoded = value.encode("utf-8")
    truncated = encoded[:max_bytes]
    while truncated:
        try:
            return truncated.decode("utf-8")
        except UnicodeDecodeError:
            truncated = truncated[:-1]
    return None


def _bounded_int(value: object, limit: int = 1 << 40) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0 or value > limit:
        return None
    return value


def _redacted_error(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return "Native acquisition reported an error."
