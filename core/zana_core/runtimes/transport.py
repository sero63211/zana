"""Bounded loopback HTTP transport using only the Python standard library."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterable, Iterator, Mapping
from typing import Any, Protocol

from zana_core.runtimes.base import (
    HttpResponse,
    InvalidRuntimeResponseError,
    RuntimeProbeError,
    RuntimeProbeTimeoutError,
)

MAX_RESPONSE_BYTES = 1_048_576
STREAM_CHUNK_SIZE = 65_536
USER_AGENT = "zana-core/0.1.0"


class TransportCleanupError(RuntimeProbeError):
    """A stream/response could not be closed deterministically."""


class UrllibTransport:
    """Safe HTTP transport with short timeouts and bounded response bodies."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout: float,
    ) -> HttpResponse:
        request_headers: dict[str, str] = {"User-Agent": USER_AGENT}
        if headers:
            request_headers.update(headers)
        request = urllib.request.Request(
            url,
            data=body,
            headers=request_headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return self._read_response(response)
        except urllib.error.HTTPError as error:
            return self._read_http_error(error)
        except TimeoutError as error:
            raise RuntimeProbeTimeoutError(
                f"Runtime probe timed out after {timeout:g}s."
            ) from error
        except urllib.error.URLError as error:
            reason = error.reason
            raise RuntimeProbeTimeoutError(
                f"Runtime endpoint could not be reached ({reason})."
            ) from error

    def _read_response(self, response) -> HttpResponse:  # noqa: ANN001
        payload = response.read(MAX_RESPONSE_BYTES + 1)
        if len(payload) > MAX_RESPONSE_BYTES:
            raise InvalidRuntimeResponseError(
                "Runtime response exceeded the 1 MiB bounded probe limit."
            )
        return HttpResponse(
            status=response.status,
            text=payload.decode("utf-8", errors="replace"),
            content_type=response.headers.get("Content-Type"),
            headers=dict(response.headers.items()),
        )

    def _read_http_error(self, error: urllib.error.HTTPError) -> HttpResponse:  # noqa: ANN001
        try:
            payload = error.read(MAX_RESPONSE_BYTES + 1)
        finally:
            error.close()
        if len(payload) > MAX_RESPONSE_BYTES:
            raise InvalidRuntimeResponseError(
                "Runtime error response exceeded the 1 MiB bounded probe limit."
            )
        return HttpResponse(
            status=error.code,
            text=payload.decode("utf-8", errors="replace"),
            content_type=error.headers.get("Content-Type"),
            headers=dict(error.headers.items()),
        )

    @staticmethod
    def json_body(payload: Mapping[str, object] | list[object]) -> bytes:
        return json.dumps(payload, separators=(",", ":")).encode("utf-8")


class StreamTransport(Protocol):
    """Injected streaming transport for bounded inference response bodies.

    ``open_stream`` yields bounded UTF-8 chunks and never buffers the whole
    body; each chunk is bounded and the total is capped by the transport. The
    caller is responsible for draining the iterator and calling ``close``.
    """

    def open_stream(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout: float,
    ) -> Iterable[bytes]: ...

    def close(self) -> None: ...


class _BoundedStream:
    """One bounded urllib response; drained lazily and closed deterministically."""

    def __init__(self, response: Any, *, max_bytes: int, chunk_size: int) -> None:
        self._response = response
        self._max_bytes = max_bytes
        self._chunk_size = chunk_size
        self._closed = False
        self._total = 0

    def __iter__(self) -> Iterator[bytes]:
        try:
            while not self._closed:
                chunk = self._response.read(self._chunk_size)
                if not chunk:
                    return
                self._total += len(chunk)
                if self._total > self._max_bytes:
                    raise InvalidRuntimeResponseError(
                        "Runtime chat stream exceeded the 1 MiB bounded limit."
                    )
                yield chunk
        finally:
            self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._response.close()
        except Exception as error:  # noqa: BLE001 - cleanup is reported, never silent
            raise TransportCleanupError("Runtime chat stream cleanup failed.") from error


class UrllibStreamTransport(UrllibTransport):
    """Bounded streaming transport with the same timeout and body caps.

    Reuses the canonical 1 MiB bounded response policy so inference streams
    are subject to the same hard cap as discovery probes. Non-2xx responses
    and timeouts are rejected without exposing response bodies or secrets.
    """

    def __init__(self) -> None:
        self._active_stream: _BoundedStream | None = None

    def open_stream(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout: float,
    ) -> Iterable[bytes]:
        request_headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/x-ndjson, application/json",
        }
        if headers:
            request_headers.update(headers)
        request = urllib.request.Request(
            url,
            data=body,
            headers=request_headers,
            method=method,
        )
        try:
            response = urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            try:
                error.read(MAX_RESPONSE_BYTES + 1)
            finally:
                error.close()
            raise InvalidRuntimeResponseError(
                f"Runtime chat returned HTTP {error.code}; runtime was not available."
            ) from error
        except TimeoutError as error:
            raise RuntimeProbeTimeoutError(f"Runtime chat timed out after {timeout:g}s.") from error
        except urllib.error.URLError as error:
            reason = error.reason
            raise RuntimeProbeTimeoutError(
                f"Runtime chat endpoint could not be reached ({reason})."
            ) from error
        stream = _BoundedStream(
            response,
            max_bytes=MAX_RESPONSE_BYTES,
            chunk_size=STREAM_CHUNK_SIZE,
        )
        self._active_stream = stream
        return stream

    def close(self) -> None:
        stream = self._active_stream
        self._active_stream = None
        if stream is not None:
            stream.close()
