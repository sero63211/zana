"""Production loopback native streaming transport using the standard library."""

from __future__ import annotations

import ipaddress
import math
import threading
import urllib.error
import urllib.request
from collections.abc import Iterable, Iterator, Mapping
from typing import Any
from urllib.parse import urlsplit

from zana_core.acquisition.limits import AcquisitionLimits

PULL_PATH = "/api/pull"
PULL_METHOD = "POST"
USER_AGENT = "zana-core/0.1.0"
STREAM_CHUNK_SIZE = 8192
MAX_URL_BYTES = 4096
MAX_BODY_BYTES = 4096
MAX_HEADER_VALUE_BYTES = 1024
MAX_TIMEOUT_SECONDS = 3600.0
_ALLOWED_HEADERS = frozenset({"content-type", "accept"})
_FORBIDDEN_HEADER_PARTS = (
    "authorization",
    "bearer",
    "token",
    "secret",
    "password",
    "api-key",
    "apikey",
    "cookie",
    "proxy",
)


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


def _validate_request(
    method: str,
    headers: Mapping[str, str] | None,
    body: bytes | None,
    timeout: float,
) -> None:
    if method != PULL_METHOD:
        raise NativeTransportProtocolError("Native pull method must be POST.")
    if type(body) is not bytes or not body:
        raise NativeTransportProtocolError("Native pull body is invalid.")
    if len(body) > MAX_BODY_BYTES:
        raise NativeTransportProtocolError("Native pull body exceeds the byte limit.")
    if headers is not None:
        if type(headers) is not dict:
            raise NativeTransportProtocolError("Native pull headers are invalid.")
        for name, value in headers.items():
            if type(name) is not str or type(value) is not str:
                raise NativeTransportProtocolError("Native pull headers are invalid.")
            lowered = name.lower()
            if lowered not in _ALLOWED_HEADERS:
                raise NativeTransportProtocolError("Native pull header is not allowed.")
            if any(part in lowered for part in _FORBIDDEN_HEADER_PARTS):
                raise NativeTransportProtocolError("Native pull header is not allowed.")
            if not value or len(value.encode("utf-8")) > MAX_HEADER_VALUE_BYTES:
                raise NativeTransportProtocolError("Native pull header value is invalid.")
            if any(ord(char) < 32 and char not in "\t" for char in value):
                raise NativeTransportProtocolError("Native pull header value is invalid.")
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
        self._total = 0
        self._closed = False

    def __iter__(self) -> Iterator[bytes]:
        try:
            while not self._closed:
                chunk = self._response.read(self._chunk_size)
                if not chunk:
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
        if self._closed:
            return
        self._closed = True
        try:
            self._response.close()
        except Exception:  # noqa: BLE001 - cleanup is reported, never silent
            raise NativeTransportCleanupError("Native pull stream cleanup failed.") from None


class UrllibNativeStreamTransport:
    """Native Ollama pull transport restricted to loopback `/api/pull` POSTs."""

    def __init__(self, limits: AcquisitionLimits | None = None) -> None:
        self.limits = limits or AcquisitionLimits()
        self._lock = threading.Lock()
        self._active_stream: _BoundedNativeStream | None = None

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
        with self._lock:
            if self._active_stream is not None:
                raise NativeTransportProtocolError(
                    "Only one native pull stream can be open at a time."
                )
            try:
                response = urllib.request.urlopen(request, timeout=timeout)
            except urllib.error.HTTPError as error:
                try:
                    error.read(1_048_576 + 1)
                finally:
                    error.close()
                raise NativeTransportHTTPError("Native runtime returned an HTTP error.") from None
            except TimeoutError as error:
                raise NativeTransportTimeoutError(
                    "Native runtime did not answer within the bounded timeout."
                ) from error
            except urllib.error.URLError:
                raise NativeTransportTimeoutError("Native runtime could not be reached.") from None
            except OSError:
                raise NativeTransportError("Native runtime transport failed.") from None
            stream = _BoundedNativeStream(
                response,
                max_bytes=self.limits.max_total_event_bytes + self.limits.max_line_bytes,
                chunk_size=STREAM_CHUNK_SIZE,
            )
            self._active_stream = stream
        return stream

    def close(self) -> None:
        with self._lock:
            stream = self._active_stream
            self._active_stream = None
        if stream is not None:
            stream.close()
