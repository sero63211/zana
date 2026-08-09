"""Bounded loopback HTTP transport using only the Python standard library."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping

from zana_core.runtimes.base import (
    HttpResponse,
    InvalidRuntimeResponseError,
    RuntimeProbeTimeoutError,
)

MAX_RESPONSE_BYTES = 1_048_576
USER_AGENT = "zana-core/0.1.0"


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
        payload = error.read(MAX_RESPONSE_BYTES + 1)
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
