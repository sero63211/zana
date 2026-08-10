"""Production loopback native streaming transport using the standard library."""

from __future__ import annotations

import ipaddress
import json
import math
import threading
import urllib.error
import urllib.request
from collections.abc import Iterable, Iterator, Mapping
from typing import Any
from urllib.parse import urlsplit

from zana_core.acquisition.limits import AcquisitionLimits
from zana_core.acquisition.redact import sanitize_model_reference

PULL_PATH = "/api/pull"
PULL_METHOD = "POST"
USER_AGENT = "zana-core/0.1.0"
STREAM_CHUNK_SIZE = 8192
MAX_URL_BYTES = 4096
MAX_BODY_BYTES = 4096
MAX_HEADER_VALUE_BYTES = 1024
MAX_TIMEOUT_SECONDS = 3600.0
MAX_IO_TIMEOUT_SECONDS = 4.0
_CONTENT_TYPE_HEADER = "content-type"
_EXPECTED_CONTENT_TYPE = "application/json"


class NativeTransportError(RuntimeError):
    """Base class for sanitized native transport failures."""


class NativeTransportProtocolError(NativeTransportError):
    """The pull request is unsafe or not an exact Ollama loopback POST."""


class NativeTransportTimeoutError(NativeTransportError):
    """The native runtime did not answer within the bounded timeout."""


class NativeTransportHTTPError(NativeTransportError):
    """The native runtime returned a non-2xx HTTP response."""


class NativeTransportCleanupError(NativeTransportError):
    """The underlying stream could not be closed deterministically."""


def _validate_pull_url(url: str) -> None:
    """Reject remote, credential, fragment, query, or non-pull endpoints."""
    if type(url) is not str or not url:
        raise NativeTransportProtocolError("Native pull URL is invalid.")
    if len(url) > MAX_URL_BYTES or len(url.encode("utf-8")) > MAX_URL_BYTES:
        raise NativeTransportProtocolError("Native pull URL exceeds the byte limit.")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise NativeTransportProtocolError("Native pull URL is malformed.") from None
    if parts.scheme not in ("http", "https"):
        raise NativeTransportProtocolError("Native pull URL must be http(s).")
    if parts.username is not None or parts.password is not None:
        raise NativeTransportProtocolError("Native pull URL must not contain credentials.")
    if parts.fragment:
        raise NativeTransportProtocolError("Native pull URL must not contain a fragment.")
    if parts.query:
        raise NativeTransportProtocolError("Native pull URL must not contain a query string.")
    if parts.path != PULL_PATH:
        raise NativeTransportProtocolError("Native pull URL must target the exact pull path.")
    if port is not None and not (1 <= port <= 65535):
        raise NativeTransportProtocolError("Native pull URL port is invalid.")
    host = parts.hostname or ""
    if host.lower() == "localhost":
        return
    ip_host = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        loopback = ipaddress.ip_address(ip_host).is_loopback
    except ValueError:
        raise NativeTransportProtocolError("Native pull URL must target a loopback host.") from None
    if not loopback:
        raise NativeTransportProtocolError("Native pull URL must target a loopback host.")


def _validate_pull_body(body: bytes | None) -> None:
    """Validate an exact JSON object containing only model and stream=true."""
    if type(body) is not bytes or not body:
        raise NativeTransportProtocolError("Native pull body is invalid.")
    if len(body) > MAX_BODY_BYTES:
        raise NativeTransportProtocolError("Native pull body exceeds the byte limit.")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise NativeTransportProtocolError("Native pull body is invalid.") from None
    seen_keys: list[str] = []

    def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        seen_keys.extend(key for key, _ in pairs)
        return dict(pairs)

    try:
        payload = json.loads(text, object_pairs_hook=object_pairs)
    except ValueError:
        raise NativeTransportProtocolError("Native pull body is invalid.") from None
    if type(payload) is not dict:
        raise NativeTransportProtocolError("Native pull body must be a JSON object.")
    if len(seen_keys) != 2 or set(seen_keys) != {"model", "stream"}:
        raise NativeTransportProtocolError("Native pull body keys are invalid.")
    if payload.get("stream") is not True:
        raise NativeTransportProtocolError("Native pull body stream must be true.")
    try:
        sanitize_model_reference(payload.get("model", ""))
    except ValueError:
        raise NativeTransportProtocolError("Native pull body model is invalid.") from None


def _validate_request(
    method: str,
    headers: Mapping[str, str] | None,
    body: bytes | None,
    timeout: float,
) -> None:
    if method != PULL_METHOD:
        raise NativeTransportProtocolError("Native pull method must be POST.")
    if type(headers) is not dict or len(headers) != 1:
        raise NativeTransportProtocolError("Native pull headers are invalid.")
    for name, value in headers.items():
        if type(name) is not str or type(value) is not str:
            raise NativeTransportProtocolError("Native pull headers are invalid.")
        if name.lower() != _CONTENT_TYPE_HEADER:
            raise NativeTransportProtocolError("Native pull header is not allowed.")
        if value != _EXPECTED_CONTENT_TYPE:
            raise NativeTransportProtocolError("Native pull content type is invalid.")
        if len(value.encode("utf-8")) > MAX_HEADER_VALUE_BYTES:
            raise NativeTransportProtocolError("Native pull header value is invalid.")
        if any((ord(char) < 32 and char not in "\t") or ord(char) == 127 for char in value):
            raise NativeTransportProtocolError("Native pull header value is invalid.")
    _validate_pull_body(body)
    if type(timeout) not in (int, float):
        raise NativeTransportProtocolError("Native pull timeout is invalid.")
    numeric = float(timeout)
    if math.isnan(numeric) or math.isinf(numeric) or numeric <= 0:
        raise NativeTransportProtocolError("Native pull timeout is invalid.")
    if numeric > MAX_TIMEOUT_SECONDS:
        raise NativeTransportProtocolError("Native pull timeout exceeds the limit.")


class _BoundedNativeStream:
    """Lazily drains one bounded response and closes deterministically."""

    def __init__(self, response: Any, *, max_bytes: int, chunk_size: int) -> None:
        self._response = response
        self._max_bytes = max_bytes
        self._chunk_size = chunk_size
        self._lock = threading.Lock()
        self._total = 0
        self._closed = False
        self._close_error: NativeTransportCleanupError | None = None

    def __iter__(self) -> Iterator[bytes]:
        try:
            while True:
                with self._lock:
                    if self._closed:
                        return
                chunk = self._response.read(self._chunk_size)
                if not chunk:
                    self.close()
                    return
                with self._lock:
                    if self._closed:
                        return
                    self._total += len(chunk)
                    if self._total > self._max_bytes:
                        raise NativeTransportProtocolError(
                            "Native pull stream exceeded the bounded size limit."
                        )
                yield chunk
        finally:
            self.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                error = self._close_error
                if error is not None:
                    raise error
                return
            self._closed = True
        try:
            self._response.close()
        except Exception:  # noqa: BLE001 - cleanup is reported, never silent
            with self._lock:
                if self._close_error is None:
                    self._close_error = NativeTransportCleanupError(
                        "Native pull stream cleanup failed."
                    )
            raise self._close_error from None


class UrllibNativeStreamTransport:
    """Native Ollama pull transport restricted to loopback `/api/pull` POSTs."""

    def __init__(self, limits: AcquisitionLimits | None = None) -> None:
        self.limits = limits or AcquisitionLimits()
        self._lock = threading.Lock()
        self._active_stream: _BoundedNativeStream | None = None
        self._open_generation: int | None = None
        self._generation = 0

    def open_stream(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout: float,
    ) -> Iterable[bytes]:
        _validate_pull_url(url)
        _validate_request(method, headers, body, timeout)
        request_headers: dict[str, str] = {
            "Content-Type": _EXPECTED_CONTENT_TYPE,
            "User-Agent": USER_AGENT,
            "Accept": "application/x-ndjson, application/json",
        }
        request = urllib.request.Request(
            url,
            data=body,
            headers=request_headers,
            method=method,
        )
        with self._lock:
            if self._active_stream is not None or self._open_generation is not None:
                raise NativeTransportProtocolError(
                    "Only one native pull stream can be open at a time."
                )
            opening = self._generation
            self._open_generation = opening
        effective_timeout = min(float(timeout), MAX_IO_TIMEOUT_SECONDS)
        response = None
        try:
            response = urllib.request.urlopen(request, timeout=effective_timeout)
        except Exception as error:  # noqa: BLE001 - every open failure is sanitized
            self._clear_opening(opening)
            if isinstance(error, TimeoutError):
                raise NativeTransportTimeoutError(
                    "Native runtime did not answer within the bounded timeout."
                ) from None
            if isinstance(error, urllib.error.HTTPError):
                try:
                    error.close()
                except Exception:  # noqa: BLE001 - cleanup is reported, never silent
                    raise NativeTransportCleanupError(
                        "Native pull HTTP error cleanup failed."
                    ) from None
                raise NativeTransportHTTPError("Native runtime returned an HTTP error.") from None
            raise NativeTransportError("Native runtime transport failed.") from None
        stream: _BoundedNativeStream | None = None
        with self._lock:
            stale = self._open_generation != opening or self._active_stream is not None
            if not stale:
                stream = _BoundedNativeStream(
                    response,
                    max_bytes=self.limits.max_total_event_bytes + self.limits.max_line_bytes,
                    chunk_size=STREAM_CHUNK_SIZE,
                )
                self._active_stream = stream
                self._open_generation = None
        if stale:
            try:
                response.close()
            except Exception:  # noqa: BLE001 - late cleanup is reported
                raise NativeTransportCleanupError("Native pull stream cleanup failed.") from None
            raise NativeTransportProtocolError("Native pull stream was invalidated before opening.")
        assert stream is not None
        return stream

    def _clear_opening(self, opening: int) -> None:
        with self._lock:
            if self._open_generation == opening:
                self._open_generation = None

    def close(self) -> None:
        with self._lock:
            self._generation += 1
            stream = self._active_stream
            self._active_stream = None
            self._open_generation = None
        if stream is not None:
            stream.close()
